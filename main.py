import asyncio
from pathlib import Path
from telegram.error import BadRequest, RetryAfter
from core.config import settings
from core.mt5_gateway import MT5Gateway
from core.engine import Engine
from storage.db import DB
from bot.telegram_app import TelegramUI

async def main():
 db=DB(settings.db_path)
 await db.init()

 gw=MT5Gateway()

 # استرجاع حساب MT5 المحفوظ إن وجد
 import json, os
 cred_file=Path.home()/'.mt5bot_credentials.json'
 if cred_file.exists():
  try:
   cred=json.loads(cred_file.read_text())
   ok,err,a=gw.login(cred['login'],cred['password'],cred['server'])
   print('MT5:',f'connected {a.login} {a.server}' if ok else f'saved login failed: {err}')
  except Exception as ex:
   print('MT5: saved login error:',ex)
 else:
  print('Telegram: starting - MT5 login available from Telegram')

 app_holder={}
 trade_messages={}
 blocked_until={'until':0.0}

 async def notify(text,photo_path=None,caption=None,trade_ticket=None,trade_update=False,pin=False):
  import time
  app=app_holder.get('app')
  if not app:return
  chat_id=settings.telegram_allowed_user_id
  now=time.monotonic()
  if now < blocked_until['until']:
   return

  try:
   if photo_path:
    with open(photo_path,'rb') as photo:
     msg=await app.bot.send_photo(chat_id=chat_id,photo=photo,caption=caption or text)
    if trade_ticket is not None:
     trade_messages[int(trade_ticket)]={'message_id':msg.message_id,'caption':caption or text,'last_edit':0.0}
    if pin:
     try:
      await app.bot.pin_chat_message(chat_id=chat_id,message_id=msg.message_id,disable_notification=True)
     except RetryAfter as ex:
      blocked_until['until']=time.monotonic()+float(ex.retry_after)+5
      print(f'Telegram pin paused due to flood control: {ex.retry_after}')
     except BadRequest as ex:
      print('Telegram pin failed:',ex)
     except Exception as ex:
      print('Telegram pin failed:',ex)
    try:
     Path(photo_path).unlink(missing_ok=True)
    except Exception:
     pass
    return

   if trade_update and trade_ticket is not None:
    state=trade_messages.get(int(trade_ticket))
    if not state:return
    # Final close result must always replace the live price immediately.
    is_final=('🏁 النتيجة:' in text)
    if not is_final and now-state.get('last_edit',0.0)<5.0:return
    if state.get('caption')==text:return
    await app.bot.edit_message_caption(chat_id=chat_id,message_id=state['message_id'],caption=text)
    state['caption']=text
    state['last_edit']=now
    return

   msg=await app.bot.send_message(chat_id=chat_id,text=text)
   async def expire():
    await asyncio.sleep(30)
    try: await app.bot.delete_message(chat_id=chat_id,message_id=msg.message_id)
    except Exception: pass
   asyncio.create_task(expire())
  except RetryAfter as ex:
   wait=float(ex.retry_after)
   blocked_until['until']=time.monotonic()+wait+5
   print(f'Telegram paused for {wait:.0f}s due to flood control')
  except BadRequest as ex:
   print(f'Telegram update failed: {ex}')
  except Exception as ex:
   print(f'Telegram notify failed: {ex}')
  finally:
   if photo_path:
    try:
     Path(photo_path).unlink(missing_ok=True)
    except Exception:
     pass

 e=Engine(gw,db,notify)
 await e.load_settings()

 # استرجاع إعدادات Telegram المحفوظة
 import json

 raw_symbols=await db.get('symbols')
 if raw_symbols:
  try:
   saved=json.loads(raw_symbols)
   if isinstance(saved,list) and saved:
    e.symbols=[str(x) for x in saved]
    e.symbol=e.symbols[0]
  except Exception:
   pass
 else:
  old_symbol=await db.get('symbol')
  if old_symbol:
   e.symbols=[old_symbol]
   e.symbol=old_symbol

 ui=TelegramUI(e,db)

 app=ui.app()
 app_holder['app']=app

 await app.initialize()
 await app.start()
 await app.updater.start_polling(drop_pending_updates=True)

 try:
  while True:
   await asyncio.sleep(3600)
 finally:
  await app.updater.stop()
  await app.stop()
  await app.shutdown()

if __name__=='__main__':
 asyncio.run(main())
