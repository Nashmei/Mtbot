import asyncio
from pathlib import Path

from telegram.error import BadRequest, RetryAfter

from api.events import EventHub
from bot.telegram_app import TelegramUI
from core.config import settings
from core.engine import Engine
from core.mt5_gateway import MT5Gateway
from storage.db import DB


async def main():
 db=DB(settings.db_path)
 await db.init()

 gw=MT5Gateway()

 # استرجاع حساب MT5 المحفوظ إن وجد
 import json
 cred_file=Path.home()/'.mt5bot_credentials.json'
 if cred_file.exists():
  try:
   cred=json.loads(cred_file.read_text())
   ok,err,a=gw.login(cred['login'],cred['password'],cred['server'])
   print('MT5:',f'connected {a.login} {a.server}' if ok else f'saved login failed: {err}')
  except Exception as ex:
   print('MT5: saved login error:',ex)
 else:
  print('MT5: no saved account; login is available from an enabled control surface')

 event_hub=EventHub()
 app_holder={}
 trade_messages={}
 blocked_until={'until':0.0}

 async def notify(text,photo_path=None,caption=None,trade_ticket=None,trade_update=False,pin=False,trade_result=None,trade_result_reason=None):
  import time

  # T4Bot events are independent of Telegram availability/flood limits.
  await event_hub.broadcast(
   'engine_notification',
   {
    'text':text,
    'trade_ticket':trade_ticket,
    'trade_update':bool(trade_update),
    'trade_result':trade_result,
    'trade_result_reason':trade_result_reason,
   }
  )

  app=app_holder.get('app')
  if not app:
   if photo_path:
    try:
     Path(photo_path).unlink(missing_ok=True)
    except Exception:
     pass
   return

  chat_id=settings.telegram_allowed_user_id
  now=time.monotonic()
  if now < blocked_until['until']:
   if photo_path:
    try:
     Path(photo_path).unlink(missing_ok=True)
    except Exception:
     pass
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
    if trade_result is not None:
     pnl=float(trade_result)
     suffix=f' | {trade_result_reason}' if trade_result_reason else ''
     result_text=(f'🟢 ربح $+{pnl:.2f}{suffix}' if pnl>0 else
                  f'🔴 خسارة $-{abs(pnl):.2f}{suffix}' if pnl<0 else
                  f'📍 تعادل $0.00{suffix}')
     result_msg=await app.bot.send_message(chat_id=chat_id,text=result_text,reply_to_message_id=state['message_id'])
     try:
      await app.bot.unpin_chat_message(chat_id=chat_id,message_id=state['message_id'])
     except BadRequest as ex:
      print('Telegram unpin trade message failed:',ex)
     try:
      await app.bot.pin_chat_message(chat_id=chat_id,message_id=result_msg.message_id,disable_notification=True)
     except BadRequest as ex:
      print('Telegram pin result failed:',ex)
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
 # Existing saved account: migrate the old global preferences once into this
 # account's profile. Newly linked accounts do not inherit another account.
 await e.load_settings(migrate_legacy=bool(gw.account()))

 api_server=None
 api_task=None
 if settings.control_api_enabled:
  if not settings.control_api_token:
   raise RuntimeError('CONTROL_API_ENABLED=true requires CONTROL_API_TOKEN')
  from api.control_api import ControlAPI
  import uvicorn

  control_api=ControlAPI(e,db,gw,event_hub,settings.control_api_token)
  api_config=uvicorn.Config(
   control_api.app,
   host=settings.control_api_host,
   port=settings.control_api_port,
   loop='asyncio',
   access_log=False,
   log_level='info',
  )
  api_server=uvicorn.Server(api_config)
  api_task=asyncio.create_task(api_server.serve())
  await asyncio.sleep(.25)
  if api_task.done():
   await api_task
  print(f'T4Bot API: listening on {settings.control_api_host}:{settings.control_api_port}')

 telegram_app=None
 if settings.telegram_enabled:
  if not settings.telegram_bot_token or settings.telegram_allowed_user_id is None:
   print('Telegram: disabled at runtime because token/user id is missing')
  else:
   ui=TelegramUI(e,db)
   telegram_app=ui.app()
   app_holder['app']=telegram_app
   await telegram_app.initialize()
   await telegram_app.start()
   await telegram_app.updater.start_polling(drop_pending_updates=True)
   print('Telegram: polling started')
 else:
  print('Telegram: disabled by TELEGRAM_ENABLED=false')

 try:
  while True:
   await asyncio.sleep(3600)
 finally:
  if telegram_app:
   await telegram_app.updater.stop()
   await telegram_app.stop()
   await telegram_app.shutdown()
  if api_server:
   api_server.should_exit=True
  if api_task:
   await api_task


if __name__=='__main__':
 asyncio.run(main())
