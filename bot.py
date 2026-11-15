# -*- coding: utf-8 -*-
"""FineVPN Telegram bot. Shares SQLite with the Mini App backend."""
import os, asyncio, html, logging, time
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, BotCommand
from aiogram.filters.command import CommandObject
from app import db, init_db, now, WEBAPP_URL, BOT_TOKEN, BOT_USERNAME, ADMIN_IDS, SUPPORT_USERNAME, setting, probe_main_panel, MAIN_PANEL_PROVIDER, audit

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("finevpn-bot")
router=Router()


def user_lang(uid):
    # FineVPN is Persian-only.
    with db() as c:
        c.execute("UPDATE users SET language='fa' WHERE id=?",(uid,))
    return "fa"

def register(tg_user, start_param=""):
    uid=tg_user.id
    ref_by=None
    with db() as c:
        exists=c.execute("SELECT id FROM users WHERE id=?",(uid,)).fetchone()
        if not exists:
            if start_param.startswith("ref_"):
                try: rid=int(start_param[4:])
                except ValueError: rid=0
                if rid and rid!=uid and c.execute("SELECT 1 FROM users WHERE id=?",(rid,)).fetchone(): ref_by=rid
            c.execute("INSERT INTO users(id,first_name,last_name,username,language,ref_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (uid,tg_user.first_name or "",tg_user.last_name or "",tg_user.username or "","fa",ref_by,now(),now()))
            if ref_by:
                reward=int(setting("points_per_referral","2"))
                c.execute("UPDATE users SET points=points+? WHERE id=?",(reward,ref_by))
                c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(ref_by,"دعوت موفق",f"یک کاربر با لینک شما وارد شد؛ {reward} امتیاز دریافت کردید.",now()))
        else:
            c.execute("UPDATE users SET first_name=?,last_name=?,username=?,language='fa',updated_at=? WHERE id=?",(tg_user.first_name or "",tg_user.last_name or "",tg_user.username or "",now(),uid))
    return user_lang(uid), not bool(exists)

def app_keyboard(lang="fa",admin=False):
    label="🚀 ورود به مینی‌اپ FineVPN"
    rows=[]
    if WEBAPP_URL.startswith("https://"):
        rows.append([InlineKeyboardButton(text=label,web_app=WebAppInfo(url=WEBAPP_URL))])
    else:
        rows.append([InlineKeyboardButton(text="تنظیم لینک مینی‌اپ در .env",callback_data="noop")])
    if admin: rows.append([InlineKeyboardButton(text="🛡 مدیریت سیستم",callback_data="admin:overview")])
    rows.append([InlineKeyboardButton(text="🎧 پشتیبانی",url="https://t.me/"+SUPPORT_USERNAME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def is_admin(uid): return uid in ADMIN_IDS

@router.message(CommandStart())
async def start(message:Message,command:CommandObject):
    param=command.args or ""
    register(message.from_user,param)
    name=html.escape(message.from_user.first_name or "دوست عزیز")
    text=f"سلام {name} عزیز 🌹\nبه FineVPN خوش آمدی. خرید، ساخت کانفیگ و مدیریت سرویس‌هایت را از مینی‌اپ انجام بده."
    await message.answer(text,reply_markup=app_keyboard("fa",is_admin(message.from_user.id)))

@router.callback_query(F.data=="noop")
async def noop(cb:CallbackQuery): await cb.answer("لینک HTTPS مینی‌اپ باید در WEBAPP_URL تنظیم شود.",show_alert=True)

@router.message(Command("app"))
async def open_app(m:Message):
    register(m.from_user)
    await m.answer("برای باز کردن داشبورد FineVPN:",reply_markup=app_keyboard(user_lang(m.from_user.id),is_admin(m.from_user.id)))

@router.message(Command("wallet"))
async def wallet(m:Message):
    register(m.from_user)
    with db() as c: r=c.execute("SELECT balance,points FROM users WHERE id=?",(m.from_user.id,)).fetchone()
    txt=f"💳 موجودی کیف پول: {r['balance']:,} تومان\n⭐ امتیاز: {r['points']:,}"
    await m.answer(txt,reply_markup=app_keyboard(user_lang(m.from_user.id),is_admin(m.from_user.id)))

@router.message(Command("services"))
async def services(m:Message):
    register(m.from_user)
    with db() as c: rows=c.execute("SELECT username,provider,volume_gb,days,ip_limit,status,subscription_url,config_url,public_url FROM services WHERE user_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,)).fetchall()
    if not rows: text="هنوز سرویسی برای شما ساخته نشده است."
    else: text="\n\n".join(f"• {html.escape(r['username'])} | گروه اشتراک FineVPN\n{r['volume_gb']}GB / {r['days']} روز / {r['ip_limit']} دستگاه\nساب: {html.escape(r['subscription_url'] or '')}\nکانفیگ: {html.escape(r['config_url'] or '')}\nآمار مصرف: {html.escape(r['public_url'] or '')}" for r in rows)
    await m.answer(text,reply_markup=app_keyboard(user_lang(m.from_user.id),is_admin(m.from_user.id)))

@router.message(Command("support"))
async def support(m:Message):
    register(m.from_user)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ثبت تیکت در مینی‌اپ",web_app=WebAppInfo(url=WEBAPP_URL))],[InlineKeyboardButton(text="ارتباط مستقیم با پشتیبانی",url="https://t.me/"+SUPPORT_USERNAME)]]) if WEBAPP_URL.startswith("https://") else app_keyboard(user_lang(m.from_user.id))
    await m.answer("برای پیگیری دقیق‌تر، از بخش تیکت پشتیبانی استفاده کن.",reply_markup=kb)

@router.message(Command("admin"))
async def admin(m:Message):
    if not is_admin(m.from_user.id): return await m.answer("دسترسی مجاز نیست.")
    with db() as c:
        users=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        services=c.execute("SELECT COUNT(*) FROM services WHERE status='active'").fetchone()[0]
        pending=c.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'").fetchone()[0]
        tickets=c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    text=f"🛡 <b>مدیریت FineVPN</b>\nکاربران: {users}\nسرویس فعال: {services}\nپرداخت در انتظار: {pending}\nتیکت باز: {tickets}\n\nدستورات:\n/approvepay ID TOMAN\n/rejectpay ID\n/setbalance USER_ID AMOUNT\n/points USER_ID AMOUNT\n/block USER_ID\n/unblock USER_ID\n/replyticket ID متن\n/broadcast متن"
    await m.answer(text,reply_markup=app_keyboard("fa",True))

@router.callback_query(F.data=="admin:overview")
async def admin_overview_cb(cb:CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("دسترسی مجاز نیست",show_alert=True)
    with db() as c:
        users=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        services=c.execute("SELECT COUNT(*) FROM services WHERE status='active'").fetchone()[0]
        pending=c.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'").fetchone()[0]
        tickets=c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    await cb.message.answer(f"🛡 آمار FineVPN\nکاربران: {users}\nسرویس فعال: {services}\nپرداخت در انتظار: {pending}\nتیکت باز: {tickets}\n\nبرای مدیریت کامل، مینی‌اپ مدیر را باز کن.",reply_markup=app_keyboard("fa",True))
    await cb.answer()

@router.message(Command("approvepay"))
async def approve_payment(m:Message):
    if not is_admin(m.from_user.id): return
    parts=(m.text or "").split()
    if len(parts)!=3: return await m.answer("فرمت: /approvepay PAYMENT_ID AMOUNT_TOMAN")
    try: tid,credit=int(parts[1]),int(parts[2])
    except ValueError: return await m.answer("شناسه و مبلغ باید عدد باشند.")
    if credit<=0: return await m.answer("معادل تومانی باید مثبت باشد.")
    with db() as c:
        t=c.execute("SELECT * FROM transactions WHERE id=? AND status='pending'",(tid,)).fetchone()
        if not t: return await m.answer("پرداخت در انتظار پیدا نشد.")
        if t['kind']=='crypto_topup':
            c.execute("UPDATE users SET balance=balance+? WHERE id=?",(credit,t['user_id']))
            c.execute("INSERT INTO transactions(user_id,kind,amount,currency,status,note,created_at) VALUES(?,?,?,?,?,?,?)",(t['user_id'],'crypto_credit',credit,'IRR','completed',f'اعتبار پرداخت رمزارزی #{tid}',now()))
        c.execute("UPDATE transactions SET status='approved',note=note||? WHERE id=?",(f' | تأیید مدیر {m.from_user.id}',tid))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(t['user_id'],'پرداخت تأیید شد',f'{credit:,} تومان به کیف پول اضافه شد.',now()))
    await m.answer(f"پرداخت #{tid} تأیید شد؛ {credit:,} تومان اضافه شد.")

@router.message(Command("rejectpay"))
async def reject_payment(m:Message):
    if not is_admin(m.from_user.id): return
    parts=(m.text or "").split()
    if len(parts)!=2: return await m.answer("فرمت: /rejectpay PAYMENT_ID")
    try: tid=int(parts[1])
    except ValueError: return await m.answer("شناسه باید عدد باشد.")
    with db() as c:
        t=c.execute("SELECT user_id FROM transactions WHERE id=? AND status='pending'",(tid,)).fetchone()
        if not t: return await m.answer("درخواست پیدا نشد.")
        c.execute("UPDATE transactions SET status='rejected',note=note||? WHERE id=?",(f' | رد مدیر {m.from_user.id}',tid))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(t['user_id'],'پرداخت رد شد',f'درخواست پرداخت #{tid} رد شد.',now()))
    await m.answer(f"درخواست #{tid} رد شد.")

@router.message(Command("setbalance"))
async def set_balance(m:Message):
    if not is_admin(m.from_user.id): return
    p=(m.text or "").split()
    if len(p)!=3: return await m.answer("فرمت: /setbalance USER_ID DELTA_TOMAN")
    try: uid,amount=int(p[1]),int(p[2])
    except ValueError: return await m.answer("شناسه و مبلغ باید عدد باشند.")
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone(): return await m.answer("کاربر پیدا نشد.")
        c.execute("UPDATE users SET balance=MAX(0,balance+?) WHERE id=?",(amount,uid))
        c.execute("INSERT INTO transactions(user_id,kind,amount,currency,status,note,created_at) VALUES(?,?,?,?,?,?,?)",(uid,'admin_adjust',amount,'IRR','completed',f'مدیر {m.from_user.id}',now()))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(uid,'تغییر کیف پول',f'موجودی شما {amount:+,} تومان تغییر کرد.',now()))
    await m.answer("موجودی به‌روزرسانی شد.")

@router.message(Command("points"))
async def points(m:Message):
    if not is_admin(m.from_user.id): return
    p=(m.text or "").split()
    if len(p)!=3: return await m.answer("فرمت: /points USER_ID DELTA")
    try: uid,amount=int(p[1]),int(p[2])
    except ValueError: return await m.answer("شناسه و امتیاز باید عدد باشند.")
    with db() as c: c.execute("UPDATE users SET points=MAX(0,points+?) WHERE id=?",(amount,uid))
    await m.answer("امتیاز کاربر به‌روزرسانی شد.")

async def block_user(m:Message,blocked:bool):
    if not is_admin(m.from_user.id): return
    p=(m.text or "").split()
    if len(p)!=2: return await m.answer("فرمت: /block USER_ID یا /unblock USER_ID")
    try: uid=int(p[1])
    except ValueError: return await m.answer("شناسه باید عدد باشد.")
    with db() as c: c.execute("UPDATE users SET is_blocked=? WHERE id=?",(int(blocked),uid))
    await m.answer("کاربر مسدود شد." if blocked else "کاربر فعال شد.")
@router.message(Command("block"))
async def block(m:Message): await block_user(m,True)
@router.message(Command("unblock"))
async def unblock(m:Message): await block_user(m,False)

@router.message(Command("panelstatus"))
async def panel_status(m:Message):
    if not is_admin(m.from_user.id): return
    with db() as c:
        row=c.execute("SELECT status,last_check,last_error FROM panels WHERE provider=? LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
    if not row:
        return await m.answer("رکورد پنل اصلی پیدا نشد.")
    checked=(time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(row["last_check"])) if row["last_check"] else "تست نشده")
    msg=f"پنل اصلی FineVPN\nوضعیت: {html.escape(row['status'])}\nآخرین تست: {checked}"
    if row["last_error"]: msg += "\nخطا: " + html.escape(row["last_error"][:500])
    await m.answer(msg)

@router.message(Command("paneltest"))
async def panel_test(m:Message):
    if not is_admin(m.from_user.id): return
    status_msg=await m.answer("در حال بررسی سلامت پنل اصلی و ورود API…")
    with db() as c:
        row=c.execute("SELECT id FROM panels WHERE provider=? LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
    if not row:
        return await status_msg.edit_text("رکورد پنل اصلی پیدا نشد؛ دیتابیس را بررسی کن.")
    try:
        info=await probe_main_panel()
        with db() as c:
            c.execute("UPDATE panels SET status='connected',last_check=?,last_error='',is_active=1 WHERE id=?",(now(),row["id"]))
        audit(m.from_user.id,"main_panel_test_ok",str(info))
        await status_msg.edit_text(f"✅ پنل اصلی متصل است\nنسخه: {html.escape(str(info['version']))}\nلینک‌ها: {info['links_count']} | فعال: {info['active_links']}\nدامنه: {html.escape(str(info['public_host']))}")
    except Exception as exc:
        with db() as c:
            c.execute("UPDATE panels SET status='error',last_check=?,last_error=? WHERE id=?",(now(),str(exc)[:500],row["id"]))
        audit(m.from_user.id,"main_panel_test_failed",str(exc))
        await status_msg.edit_text("❌ اتصال پنل ناموفق بود: " + html.escape(str(exc)[:700]))

@router.message(Command("replyticket"))
async def reply_ticket(m:Message):
    if not is_admin(m.from_user.id): return
    parts=(m.text or "").split(maxsplit=2)
    if len(parts)<3: return await m.answer("فرمت: /replyticket TICKET_ID متن پاسخ")
    try: tid=int(parts[1])
    except ValueError: return await m.answer("شناسه تیکت باید عدد باشد.")
    body=parts[2]
    with db() as c:
        t=c.execute("SELECT user_id FROM tickets WHERE id=?",(tid,)).fetchone()
        if not t: return await m.answer("تیکت پیدا نشد.")
        c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_role,body,created_at) VALUES(?,?,'admin',?,?)",(tid,m.from_user.id,body,now()))
        c.execute("UPDATE tickets SET status='answered',updated_at=? WHERE id=?",(now(),tid))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(t['user_id'],'پاسخ پشتیبانی',body[:200],now()))
    await m.answer("پاسخ تیکت ارسال شد.")

@router.message(Command("broadcast"))
async def broadcast(m:Message,bot:Bot):
    if not is_admin(m.from_user.id): return
    text=(m.text or "").partition(" ")[2].strip()
    if len(text)<2: return await m.answer("فرمت: /broadcast متن پیام")
    with db() as c: ids=[r[0] for r in c.execute("SELECT id FROM users WHERE is_blocked=0").fetchall()]
    ok=0; fail=0
    for uid in ids:
        try:
            await bot.send_message(uid,text)
            ok+=1
            await asyncio.sleep(.055)
        except Exception: fail+=1
    await m.answer(f"ارسال پایان یافت. موفق: {ok} | ناموفق: {fail}")

@router.message(Command("help"))
async def help_cmd(m:Message):
    txt="دستورات کاربری:\n/app - باز کردن مینی‌اپ\n/wallet - موجودی و امتیاز\n/services - سرویس‌ها\n/support - پشتیبانی\n\nمدیریت: /admin /paneltest /panelstatus"
    await m.answer(txt)

async def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN در فایل .env تنظیم نشده است")
    if not WEBAPP_URL.startswith("https://"): log.warning("WEBAPP_URL باید HTTPS باشد تا دکمه Web App در تلگرام کار کند")
    init_db()
    bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher();dp.include_router(router)
    await bot.set_my_commands([
      BotCommand(command="start",description="شروع و ورود به مینی‌اپ"),
      BotCommand(command="app",description="باز کردن FineVPN"),
      BotCommand(command="wallet",description="کیف پول"),
      BotCommand(command="services",description="سرویس‌های من"),
      BotCommand(command="support",description="پشتیبانی"),
      BotCommand(command="admin",description="مدیریت (مدیران)"),
      BotCommand(command="paneltest",description="تست اتصال پنل اصلی (مدیران)"),
      BotCommand(command="panelstatus",description="وضعیت پنل اصلی (مدیران)"),
    ])
    log.info("FineVPN bot polling started")
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
