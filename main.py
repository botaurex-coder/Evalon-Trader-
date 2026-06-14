"""Evalon Winners — Telegram trading-signals bot.
Entry point. Wires up handlers, scheduler, and a tiny aiohttp healthcheck so
the bot can run on Render free-tier web services (Render requires a port
listener).
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from aiohttp import web
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatJoinRequest,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, ChatJoinRequestHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)
import db
import engine
from config import (
    ADMIN_ID, BOT_TOKEN, CHANNEL_ID, CHANNEL_INVITE, FREE_SIGNAL_LIMIT,
    IMG_BUY, IMG_SELL, NON_OTC_PAIRS, OTC_PAIRS, PORT, SUPPORT_BOT,
)
from market import latest_price, market_is_open
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")
# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
SCAN_FRAMES = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID
async def cleanup_send(
    ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    *,
    text: Optional[str] = None,
    photo: Optional[str] = None,
    caption: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML,
) -> int:
    """Delete the user's previous bot message, send a new one, remember its id."""
    old = db.pop_msg(user_id)
    if old:
        try:
            await ctx.bot.delete_message(chat_id=user_id, message_id=old)
        except Exception:
            pass
    if photo:
        msg = await ctx.bot.send_photo(
            chat_id=user_id, photo=photo, caption=caption,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
    else:
        msg = await ctx.bot.send_message(
            chat_id=user_id, text=text or "", reply_markup=reply_markup,
            parse_mode=parse_mode, disable_web_page_preview=True,
        )
    db.push_msg(user_id, msg.message_id)
    return msg.message_id
def pairs_keyboard() -> InlineKeyboardMarkup:
    pairs = OTC_PAIRS if not market_is_open() else NON_OTC_PAIRS
    rows = []
    row = []
    for i, p in enumerate(pairs, 1):
        row.append(InlineKeyboardButton(p, callback_data=f"pair|{p}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)
def welcome_markup() -> InlineKeyboardMarkup:
    rows = []
    if db.list_brokers():
        rows.append([InlineKeyboardButton("📝 Register with Broker", callback_data="broker_menu")])
    rows.append([InlineKeyboardButton("📊 Get Signals", callback_data="show_pairs")])
    rows.append([InlineKeyboardButton("📢 Join Channel", url=CHANNEL_INVITE)])
    rows.append([InlineKeyboardButton("💬 Support", url=f"https://t.me/{SUPPORT_BOT}")])
    return InlineKeyboardMarkup(rows)
# ----------------------------------------------------------------------------
# /start
# ----------------------------------------------------------------------------
WELCOME = (
    "👋 <b>Welcome to Evalon Winners Bot</b>\n\n"
    "High-accuracy binary-options signals powered by multi-indicator AI consensus.\n\n"
    "• <b>Free trial:</b> 3 signals\n"
    "• <b>Then:</b> activate a licence code\n\n"
    "Choose an option below to get started 👇"
)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)
    if db.is_banned(u.id):
        await update.message.reply_text("🚫 You are banned from this bot.")
        return
    msg = await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=welcome_markup(),
        disable_web_page_preview=True,
    )
    db.push_msg(u.id, msg.message_id)
async def cb_show_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    if db.is_banned(u.id):
        return
    ok, why = db.can_request_signal(u.id)
    header = "📊 <b>Select a pair:</b>" if ok else why
    if not ok:
        await cleanup_send(ctx, u.id, text=header)
        return
    mode = "OTC" if not market_is_open() else "Live"
    await cleanup_send(
        ctx, u.id,
        text=f"📊 <b>Select a pair</b> ({mode} market):",
        reply_markup=pairs_keyboard(),
    )
# ----------------------------------------------------------------------------
# broker menu
# ----------------------------------------------------------------------------
async def cb_broker_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    brokers = db.list_brokers()
    if not brokers:
        await cleanup_send(ctx, q.from_user.id, text="No brokers configured yet.")
        return
    rows = [[InlineKeyboardButton(b["name"], url=b["url"])] for b in brokers]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back_home")])
    await cleanup_send(
        ctx, q.from_user.id,
        text="📝 <b>Register with a Broker</b>\n\nChoose one to open the link:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
async def cb_back_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await cleanup_send(ctx, q.from_user.id, text=WELCOME, reply_markup=welcome_markup())
# ----------------------------------------------------------------------------
# pair selection
# ----------------------------------------------------------------------------
async def cb_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pair = q.data.split("|", 1)[1]
    u = q.from_user
    if db.is_banned(u.id):
        return
    ok, why = db.can_request_signal(u.id)
    if not ok:
        await cleanup_send(ctx, u.id, text=why)
        return
    open_now = market_is_open()
    is_otc = pair.endswith(" OTC")
    if is_otc and open_now:
        await cleanup_send(
            ctx, u.id,
            text="⚠️ Live market is open. Please pick a non-OTC pair.",
            reply_markup=pairs_keyboard(),
        )
        return
    if not is_otc and not open_now:
        await cleanup_send(
            ctx, u.id,
            text="⚠️ Live market is closed. Please pick an OTC pair.",
            reply_markup=pairs_keyboard(),
        )
        return
    if is_otc:
        rows = [[
            InlineKeyboardButton("5s", callback_data=f"otc|{pair}|5"),
            InlineKeyboardButton("10s", callback_data=f"otc|{pair}|10"),
            InlineKeyboardButton("15s", callback_data=f"otc|{pair}|15"),
            InlineKeyboardButton("30s", callback_data=f"otc|{pair}|30"),
        ]]
        await cleanup_send(
            ctx, u.id,
            text=f"⏱ <b>{pair}</b>\nSelect signal duration:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    else:
        rows = [
            [InlineKeyboardButton("🤖 Auto Signal", callback_data=f"auto|{pair}")],
            [InlineKeyboardButton("⏱ Bot Picks", callback_data=f"picks|{pair}")],
        ]
        await cleanup_send(
            ctx, u.id,
            text=f"📈 <b>{pair}</b>\nChoose signal mode:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
# ----------------------------------------------------------------------------
# analyzing animation
# ----------------------------------------------------------------------------
async def scan_animation(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, pair: str) -> int:
    old = db.pop_msg(user_id)
    if old:
        try:
            await ctx.bot.delete_message(chat_id=user_id, message_id=old)
        except Exception:
            pass
    msg = await ctx.bot.send_message(chat_id=user_id, text=f"🔴 Scanning {pair}...")
    db.push_msg(user_id, msg.message_id)
    return msg.message_id
async def animate_loop(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, msg_id: int, pair: str, stop: asyncio.Event) -> None:
    i = 0
    await asyncio.sleep(0.5)
    while not stop.is_set():
        i = (i + 1) % len(SCAN_FRAMES)
        try:
            await ctx.bot.edit_message_text(
                chat_id=user_id, message_id=msg_id,
                text=f"{SCAN_FRAMES[i]} Scanning {pair}...",
            )
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
# ----------------------------------------------------------------------------
# OTC signal
# ----------------------------------------------------------------------------
def signal_caption(pair: str, direction: str, seconds: int, strength: int) -> str:
    arrow = "Up 🟢" if direction == "BUY" else "Down 🔴"
    unit = f"{seconds} sec." if seconds < 60 else f"{seconds // 60} min."
    return (
        f"<b>{pair}</b>  {arrow}\n"
        f"🕐 In {unit}\n"
        f"📊 Signal strength: {strength}%\n"
        f"🧠 AI Consensus"
    )
def signal_image(direction: str) -> str:
    return (db.get_setting("img_buy") or IMG_BUY) if direction == "BUY" else (db.get_setting("img_sell") or IMG_SELL)
async def cb_otc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, pair, secs = q.data.split("|", 2)
    seconds = int(secs)
    u = q.from_user
    ok, why = db.can_request_signal(u.id)
    if not ok:
        await cleanup_send(ctx, u.id, text=why)
        return
    msg_id = await scan_animation(ctx, u.id, pair)
    stop = asyncio.Event()
    anim = asyncio.create_task(animate_loop(ctx, u.id, msg_id, pair, stop))
    try:
        sig = await engine.analyze(pair, tf_min=1)
    except Exception as e:
        stop.set(); await anim
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get More Signal", callback_data=f"otc|{pair}|{seconds}")]])
        await cleanup_send(ctx, u.id, text=f"⚠️ Could not analyze {pair}. Try again shortly.\n<code>{e}</code>", reply_markup=kb)
        return
    stop.set()
    await anim
    if not sig:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get More Signal", callback_data=f"otc|{pair}|{seconds}")]])
        await cleanup_send(ctx, u.id, text="🟡 No strong signal right now. Try again in a few seconds.", reply_markup=kb)
        return
    if not db.has_active_licence(u.id):
        db.increment_free(u.id)
    db.record_signal(u.id, pair, sig.direction, seconds, sig.entry, sig.strength)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get More Signal", callback_data=f"otc|{pair}|{seconds}")]])
    await cleanup_send(
        ctx, u.id,
        photo=signal_image(sig.direction),
        caption=signal_caption(pair, sig.direction, seconds, sig.strength),
        reply_markup=kb,
    )
# ----------------------------------------------------------------------------
# Non-OTC: Bot Picks (one-shot)
# ----------------------------------------------------------------------------
async def cb_picks(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pair = q.data.split("|", 1)[1]
    u = q.from_user
    ok, why = db.can_request_signal(u.id)
    if not ok:
        await cleanup_send(ctx, u.id, text=why)
        return
    msg_id = await scan_animation(ctx, u.id, pair)
    stop = asyncio.Event()
    anim = asyncio.create_task(animate_loop(ctx, u.id, msg_id, pair, stop))
    try:
        best = await engine.best_timeframe(pair)
    except Exception as e:
        stop.set(); await anim
        await cleanup_send(ctx, u.id, text=f"⚠️ Could not analyze {pair}. Try again shortly.\n<code>{e}</code>")
        return
    stop.set()
    await anim
    if not best:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get More Signal", callback_data=f"picks|{pair}")]])
        await cleanup_send(ctx, u.id, text="🟡 No signal available — try again in a few minutes.", reply_markup=kb)
        return
    tf, sig = best
    if not db.has_active_licence(u.id):
        db.increment_free(u.id)
    sid = db.record_signal(u.id, pair, sig.direction, tf * 60, sig.entry, sig.strength)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Get More Signal", callback_data=f"picks|{pair}")]])
    await cleanup_send(
        ctx, u.id,
        photo=signal_image(sig.direction),
        caption=signal_caption(pair, sig.direction, tf * 60, sig.strength),
        reply_markup=kb,
    )
    ctx.application.create_task(_schedule_result(ctx, u.id, sid, pair, sig.direction, sig.entry, tf))
# ----------------------------------------------------------------------------
# Non-OTC: Auto Signal mode (continuous)
# ----------------------------------------------------------------------------
AUTO_TASKS: dict[int, asyncio.Task] = {}
async def cb_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pair = q.data.split("|", 1)[1]
    u = q.from_user
    rows = [[
        InlineKeyboardButton("1 min", callback_data=f"autotf|{pair}|1"),
        InlineKeyboardButton("5 min", callback_data=f"autotf|{pair}|5"),
    ]]
    await cleanup_send(
        ctx, u.id,
        text=f"🤖 <b>Auto Signal — {pair}</b>\nChoose expiry:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
async def cb_auto_tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, pair, tfs = q.data.split("|", 2)
    tf = int(tfs)
    u = q.from_user
    ok, why = db.can_request_signal(u.id)
    if not ok:
        await cleanup_send(ctx, u.id, text=why)
        return
    old = AUTO_TASKS.pop(u.id, None)
    if old:
        old.cancel()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop Auto", callback_data="auto_stop")]])
    await cleanup_send(
        ctx, u.id,
        text=f"🤖 Auto-scan started for <b>{pair}</b> ({tf} min). Strong signals will be sent automatically.",
        reply_markup=kb,
    )
    AUTO_TASKS[u.id] = ctx.application.create_task(_auto_loop(ctx, u.id, pair, tf))
async def cb_auto_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Stopped")
    t = AUTO_TASKS.pop(q.from_user.id, None)
    if t:
        t.cancel()
    await cleanup_send(ctx, q.from_user.id, text="⏹ Auto-scan stopped.", reply_markup=welcome_markup())
async def _auto_loop(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, pair: str, tf: int) -> None:
    try:
        while True:
            ok, _ = db.can_request_signal(user_id)
            if not ok:
                await ctx.bot.send_message(chat_id=user_id, text="🔒 Free signals exhausted. Auto-scan stopped.")
                return
            try:
                sig = await engine.analyze(pair, tf_min=tf)
            except Exception as e:
                log.warning("auto analyze error: %s", e)
                await asyncio.sleep(30); continue
            if sig and sig.strength >= 78:
                if not db.has_active_licence(user_id):
                    db.increment_free(user_id)
                sid = db.record_signal(user_id, pair, sig.direction, tf * 60, sig.entry, sig.strength)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop Auto", callback_data="auto_stop")]])
                await cleanup_send(
                    ctx, user_id,
                    photo=signal_image(sig.direction),
                    caption=signal_caption(pair, sig.direction, tf * 60, sig.strength),
                    reply_markup=kb,
                )
                await _schedule_result(ctx, user_id, sid, pair, sig.direction, sig.entry, tf)
                await asyncio.sleep(max(60, tf * 60))
            else:
                await asyncio.sleep(20)
    except asyncio.CancelledError:
        return
# ----------------------------------------------------------------------------
# auto-result evaluation (non-OTC only)
# ----------------------------------------------------------------------------
async def _schedule_result(ctx, user_id: int, sid: int, pair: str, direction: str, entry: float, tf_min: int) -> None:
    now = datetime.now(timezone.utc)
    next_minute = (now.replace(second=0, microsecond=0) + __import__("datetime").timedelta(minutes=1))
    wait_start = (next_minute - now).total_seconds()
    await asyncio.sleep(max(0, wait_start))
    await asyncio.sleep(tf_min * 60)
    exit_price = await latest_price(pair)
    if exit_price is None:
        db.finalize_signal(sid, None, "DOJI")
        return
    if entry is None:
        db.finalize_signal(sid, exit_price, "DOJI")
        return
    delta = exit_price - entry
    eps = abs(entry) * 1e-5
    if abs(delta) <= eps:
        result = "DOJI"; emoji = "➖"
    elif (direction == "BUY" and delta > 0) or (direction == "SELL" and delta < 0):
        result = "WIN"; emoji = "✅"
    else:
        result = "LOSS"; emoji = "❌"
    db.finalize_signal(sid, exit_price, result)
    text = (
        f"{emoji} <b>{result}</b> — {pair}\n"
        f"📈 Signal: {direction} | {tf_min} min\n"
        f"💰 Entry: {entry:.5f} → Exit: {exit_price:.5f}"
    )
    try:
        await cleanup_send(ctx, user_id, text=text)
    except Exception:
        pass
# ----------------------------------------------------------------------------
# licence code redemption (any non-command text)
# ----------------------------------------------------------------------------
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    text = (update.message.text or "").strip().upper()
    if text.startswith("EW-"):
        ok, msg = db.redeem_code(text, u.id)
        await update.message.reply_text(msg)
        return
    await update.message.reply_text(
        "Send /start to open the menu, or paste a licence code (format: <code>EW-XXXXXXXXXX</code>).",
        parse_mode=ParseMode.HTML,
    )
# ----------------------------------------------------------------------------
# join requests
# ----------------------------------------------------------------------------
async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    req: ChatJoinRequest = update.chat_join_request
    if req.chat.id != CHANNEL_ID:
        return
    user = req.from_user
    db.add_join_request(user.id, req.chat.id)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"jr|ok|{user.id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"jr|no|{user.id}"),
    ]])
    try:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📥 <b>New channel join request</b>\n\n"
                f"User: <a href='tg://user?id={user.id}'>{user.first_name or user.id}</a>\n"
                f"Username: @{user.username or '—'}\n"
                f"ID: <code>{user.id}</code>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except Exception as e:
        log.warning("could not notify admin of join request: %s", e)
async def cb_join_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    _, action, uid_s = q.data.split("|", 2)
    uid = int(uid_s)
    row = db.pop_join_request(uid)
    chat_id = row["chat_id"] if row else CHANNEL_ID
    try:
        if action == "ok":
            await ctx.bot.approve_chat_join_request(chat_id=chat_id, user_id=uid)
            await q.edit_message_text(q.message.text_html + "\n\n✅ <b>Approved</b>", parse_mode=ParseMode.HTML)
        else:
            await ctx.bot.decline_chat_join_request(chat_id=chat_id, user_id=uid)
            await q.edit_message_text(q.message.text_html + "\n\n❌ <b>Rejected</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await q.edit_message_text(q.message.text_html + f"\n\n⚠️ Failed: {e}", parse_mode=ParseMode.HTML)
# ----------------------------------------------------------------------------
# admin commands
# ----------------------------------------------------------------------------
ADMIN_HELP = (
    "🛠 <b>Admin Panel</b>\n\n"
    "/gencode monthly | lifetime — generate a licence code\n"
    "/revoke &lt;code&gt; — revoke a code\n"
    "/users — list users (latest first)\n"
    "/ban &lt;user_id&gt; — ban a user\n"
    "/unban &lt;user_id&gt; — unban a user\n"
    "/broadcast &lt;message&gt; — broadcast to everyone\n"
    "/setimage buy|sell &lt;url&gt; — change signal image\n"
    "/setbroker &lt;name&gt; &lt;url&gt; — add/update broker link\n"
    "/removebroker &lt;name&gt; — remove broker link\n"
    "/listbrokers — list broker links\n"
    "/dbcheck — database health\n"
    "/stats — signal stats and win rate"
)
def _admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            return
        return await func(update, ctx)
    return wrapper
@_admin_only
async def cmd_help(update, ctx):
    await update.message.reply_text(ADMIN_HELP, parse_mode=ParseMode.HTML)
@_admin_only
async def cmd_gencode(update, ctx):
    args = ctx.args
    if not args or args[0] not in ("monthly", "lifetime"):
        await update.message.reply_text("Usage: /gencode monthly | lifetime")
        return
    code = db.gen_code(args[0])
    await update.message.reply_text(
        f"✅ {args[0].title()} licence code:\n<code>{code}</code>",
        parse_mode=ParseMode.HTML,
    )
@_admin_only
async def cmd_revoke(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Usage: /revoke <code>")
        return
    ok = db.revoke_code(ctx.args[0].strip().upper())
    await update.message.reply_text("✅ Revoked." if ok else "❌ Code not found.")
@_admin_only
async def cmd_users(update, ctx):
    rows = db.all_users()[:50]
    lines = [f"👥 Total: {len(db.all_users())}", ""]
    for r in rows:
        lic = r["licence_type"] or "free"
        flag = " 🚫" if r["banned"] else ""
        lines.append(f"• <code>{r['user_id']}</code> @{r['username'] or '—'} ({lic}){flag}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
@_admin_only
async def cmd_ban(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Usage: /ban <user_id>"); return
    db.set_banned(int(ctx.args[0]), True)
    await update.message.reply_text("🚫 User banned.")
@_admin_only
async def cmd_unban(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Usage: /unban <user_id>"); return
    db.set_banned(int(ctx.args[0]), False)
    await update.message.reply_text("✅ User unbanned.")
@_admin_only
async def cmd_broadcast(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <message>"); return
    msg = " ".join(ctx.args)
    sent = failed = 0
    for u in db.all_users():
        try:
            await ctx.bot.send_message(chat_id=u["user_id"], text=msg)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    await update.message.reply_text(f"📢 Broadcast done. Sent: {sent}, Failed: {failed}")
@_admin_only
async def cmd_setimage(update, ctx):
    if len(ctx.args) < 2 or ctx.args[0] not in ("buy", "sell"):
        await update.message.reply_text("Usage: /setimage buy|sell <url>"); return
    db.set_setting(f"img_{ctx.args[0]}", ctx.args[1])
    await update.message.reply_text(f"✅ {ctx.args[0].upper()} image updated.")
@_admin_only
async def cmd_setbroker(update, ctx):
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /setbroker <name> <url>"); return
    name = ctx.args[0]
    url = " ".join(ctx.args[1:])
    db.set_broker(name, url)
    await update.message.reply_text(f"✅ Broker '{name}' saved.")
@_admin_only
async def cmd_removebroker(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Usage: /removebroker <name>"); return
    ok = db.remove_broker(ctx.args[0])
    await update.message.reply_text("✅ Removed." if ok else "❌ Not found.")
@_admin_only
async def cmd_listbrokers(update, ctx):
    rows = db.list_brokers()
    if not rows:
        await update.message.reply_text("No brokers configured."); return
    text = "🔗 <b>Brokers</b>\n\n" + "\n".join(f"• {r['name']} — {r['url']}" for r in rows)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
@_admin_only
async def cmd_dbcheck(update, ctx):
    try:
        s = db.stats()
        await update.message.reply_text(
            f"✅ DB OK\nUsers: {s['users']}\nActive licences: {s['active_licences']}\nSignals: {s['total']}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ DB error: {e}")
@_admin_only
async def cmd_stats(update, ctx):
    s = db.stats()
    await update.message.reply_text(
        f"📊 <b>Stats</b>\n\n"
        f"Users: {s['users']}\nActive licences: {s['active_licences']}\n"
        f"Signals: {s['total']}\nWins: {s['wins']}\nLosses: {s['losses']}\nDoji: {s['dojis']}\n"
        f"Win rate: {s['win_rate']:.1f}%",
        parse_mode=ParseMode.HTML,
    )
# ----------------------------------------------------------------------------
# licence expiry warnings (daily)
# ----------------------------------------------------------------------------
async def licence_warner(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    now = int(time.time())
    for u in db.all_users():
        if u["licence_type"] != "monthly" or not u["licence_exp"]:
            continue
        days_left = (u["licence_exp"] - now) / 86400
        try:
            if 2 < days_left <= 3 and not u["warn3_sent"]:
                await ctx.bot.send_message(u["user_id"], "⏳ Your licence expires in 3 days. Renew with a new code.")
                with db.db() as c:
                    c.execute("UPDATE users SET warn3_sent=1 WHERE user_id=?", (u["user_id"],))
            elif 0 < days_left <= 1 and not u["warn1_sent"]:
                await ctx.bot.send_message(u["user_id"], "⏳ Your licence expires in less than 24 hours. Renew now.")
                with db.db() as c:
                    c.execute("UPDATE users SET warn1_sent=1 WHERE user_id=?", (u["user_id"],))
        except Exception:
            pass
# ----------------------------------------------------------------------------
# health server
# ----------------------------------------------------------------------------
async def _health(_request): return web.Response(text="ok")
async def _run_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health server listening on :%d", PORT)
# ----------------------------------------------------------------------------
# bootstrap
# ----------------------------------------------------------------------------
async def post_init(app: Application) -> None:
    await _run_health_server()
    app.job_queue.run_repeating(licence_warner, interval=12 * 3600, first=60)
def build_app() -> Application:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Add it in Render → Environment.")
    db.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("gencode", cmd_gencode))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("setimage", cmd_setimage))
    app.add_handler(CommandHandler("setbroker", cmd_setbroker))
    app.add_handler(CommandHandler("removebroker", cmd_removebroker))
    app.add_handler(CommandHandler("listbrokers", cmd_listbrokers))
    app.add_handler(CommandHandler("dbcheck", cmd_dbcheck))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(cb_show_pairs, pattern=r"^show_pairs$"))
    app.add_handler(CallbackQueryHandler(cb_back_home, pattern=r"^back_home$"))
    app.add_handler(CallbackQueryHandler(cb_broker_menu, pattern=r"^broker_menu$"))
    app.add_handler(CallbackQueryHandler(cb_pair, pattern=r"^pair\|"))
    app.add_handler(CallbackQueryHandler(cb_otc, pattern=r"^otc\|"))
    app.add_handler(CallbackQueryHandler(cb_auto, pattern=r"^auto\|"))
    app.add_handler(CallbackQueryHandler(cb_auto_tf, pattern=r"^autotf\|"))
    app.add_handler(CallbackQueryHandler(cb_auto_stop, pattern=r"^auto_stop$"))
    app.add_handler(CallbackQueryHandler(cb_picks, pattern=r"^picks\|"))
    app.add_handler(CallbackQueryHandler(cb_join_decision, pattern=r"^jr\|"))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
if __name__ == "__main__":
    main()
