#!/usr/bin/env python3
"""
Portfolio Tracker Bot — Reglas del Padre
Monitorea precios cada 15 minutos y envía alertas a Telegram
cuando se activa una regla de compra o venta.
"""

import asyncio
import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path

import httpx
from telegram import Bot
from telegram.constants import ParseMode

# ─────────────────────────────────────────
#  CONFIGURACIÓN — Edita estos valores
# ─────────────────────────────────────────
TELEGRAM_TOKEN = "8731757900:AAHbszkpSoKT7JIgd8i1IL7P3TzdfqLkr3Q"       # Token de tu bot (de @BotFather)
TELEGRAM_CHAT_ID = "2051012633"  # Tu chat ID (de @userinfobot)
CHECK_INTERVAL_MINUTES = 15            # Cada cuántos minutos revisar

# Archivo donde se guardan tus activos (se crea automático)
PORTFOLIO_FILE = Path("portfolio.json")

# ─────────────────────────────────────────
#  REGLAS DEL PADRE
# ─────────────────────────────────────────
DOWN_RULES = [
    {"pct": -10, "action": "HOLD", "label": "Mantén",      "extra_pct": 0},
    {"pct": -20, "action": "BUY",  "label": "Compra +15%", "extra_pct": 15},
    {"pct": -30, "action": "BUY",  "label": "Compra +30%", "extra_pct": 30},
]
UP_RULES = [
    {"pct":  10, "action": "HOLD", "label": "Mantén",     "sell_pct": 0},
    {"pct":  20, "action": "HOLD", "label": "Mantén",     "sell_pct": 0},
    {"pct":  30, "action": "SELL", "label": "Vende 10%",  "sell_pct": 10},
    {"pct":  40, "action": "SELL", "label": "Vende 20%",  "sell_pct": 20},
    {"pct":  50, "action": "SELL", "label": "Vende 30%",  "sell_pct": 30},
    {"pct":  60, "action": "SELL", "label": "Vende 40%",  "sell_pct": 40},
    {"pct": 100, "action": "SELL", "label": "Vende 60%",  "sell_pct": 60},
]

# ─────────────────────────────────────────
#  PORTFOLIO POR DEFECTO (edita a tu gusto)
# ─────────────────────────────────────────
DEFAULT_PORTFOLIO = [
    {
        "id": 1,
        "name": "Bitcoin",
        "ticker": "BTC",
        "type": "crypto",
        "coingecko_id": "bitcoin",      # ID en CoinGecko
        "entry_price": 68500,
        "capital": 1000,
        "units": round(1000 / 68500, 8),
        "last_alert_rule": None,        # Para no repetir la misma alerta
    },
    {
        "id": 2,
        "name": "Oro",
        "ticker": "XAU",
        "type": "gold",
        "coingecko_id": None,           # El oro usa otra API
        "entry_price": 3100,
        "capital": 500,
        "units": round(500 / 3100, 6),
        "last_alert_rule": None,
    },
    # Agrega más activos aquí:
    # {
    #     "id": 3,
    #     "name": "Ethereum",
    #     "ticker": "ETH",
    #     "type": "crypto",
    #     "coingecko_id": "ethereum",
    #     "entry_price": 2000,
    #     "capital": 500,
    #     "units": round(500 / 2000, 8),
    #     "last_alert_rule": None,
    # },
]

# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("portfolio_bot.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
#  CARGA / GUARDA PORTFOLIO
# ─────────────────────────────────────────
def load_portfolio():
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    portfolio = DEFAULT_PORTFOLIO.copy()
    save_portfolio(portfolio)
    return portfolio

def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────
#  OBTENER PRECIOS
# ─────────────────────────────────────────
async def get_crypto_prices(coingecko_ids: list[str]) -> dict:
    """Obtiene precios de CoinGecko (gratis, sin API key)."""
    ids_str = ",".join(coingecko_ids)
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.error(f"Error CoinGecko: {e}")
        return {}

async def get_gold_price() -> float | None:
    """Obtiene precio del oro via metals.live (gratis)."""
    url = "https://metals.live/api/v1/spot"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            # metals.live devuelve lista: [{metal: "gold", price: ...}, ...]
            for item in data:
                if item.get("metal", "").lower() == "gold":
                    return float(item["price"])
    except Exception as e:
        log.error(f"Error metals.live: {e}")
    # Fallback: goldapi alternativa
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.gold-api.com/price/XAU")
            r.raise_for_status()
            data = r.json()
            return float(data.get("price", 0))
    except Exception as e:
        log.error(f"Error gold-api fallback: {e}")
    return None

async def fetch_all_prices(portfolio: list) -> dict:
    """Retorna {ticker: precio_actual} para todos los activos."""
    prices = {}

    # Crypto
    crypto_ids = [a["coingecko_id"] for a in portfolio if a.get("coingecko_id")]
    if crypto_ids:
        cg_data = await get_crypto_prices(crypto_ids)
        for asset in portfolio:
            cg_id = asset.get("coingecko_id")
            if cg_id and cg_id in cg_data:
                prices[asset["ticker"]] = cg_data[cg_id]["usd"]

    # Oro
    for asset in portfolio:
        if asset["type"] == "gold" and asset["ticker"] == "XAU":
            gold_price = await get_gold_price()
            if gold_price:
                prices["XAU"] = gold_price

    return prices

# ─────────────────────────────────────────
#  LÓGICA DE REGLAS
# ─────────────────────────────────────────
def get_active_rule(change_pct: float) -> dict | None:
    """Devuelve la regla activa según el % de cambio."""
    if change_pct < 0:
        active = None
        for rule in DOWN_RULES:
            if change_pct <= rule["pct"]:
                active = {**rule, "side": "down"}
        return active
    else:
        active = None
        for rule in UP_RULES:
            if change_pct >= rule["pct"]:
                active = {**rule, "side": "up"}
        return active

def format_usd(n: float) -> str:
    return f"${n:,.2f}"

# ─────────────────────────────────────────
#  MENSAJES DE TELEGRAM
# ─────────────────────────────────────────
def build_alert_message(asset: dict, current_price: float, change_pct: float, rule: dict) -> str:
    action = rule["action"]
    label = rule["label"]
    capital = asset["capital"]
    units = asset["units"]
    current_value = units * current_price
    pnl = current_value - capital
    pnl_str = f"+{format_usd(pnl)}" if pnl >= 0 else format_usd(pnl)

    arrow = "🔴📉" if rule["side"] == "down" else "🟢📈"
    action_emoji = {"BUY": "💰", "SELL": "💸", "HOLD": "🤚"}[action]

    extra_info = ""
    if action == "BUY" and rule.get("extra_pct", 0) > 0:
        extra_usd = capital * rule["extra_pct"] / 100
        extra_info = f"\n💵 *Monto sugerido a comprar:* {format_usd(extra_usd)}"
    elif action == "SELL" and rule.get("sell_pct", 0) > 0:
        sell_usd = current_value * rule["sell_pct"] / 100
        sell_units = units * rule["sell_pct"] / 100
        extra_info = f"\n💵 *Monto sugerido a vender:* {format_usd(sell_usd)} ({sell_units:.6f} {asset['ticker']})"

    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    msg = f"""
{arrow} *ALERTA DE PORTFOLIO* {arrow}

*{asset['name']}* ({asset['ticker']})
━━━━━━━━━━━━━━━━━━━━

📌 *Precio entrada:* {format_usd(asset['entry_price'])}
📊 *Precio actual:* {format_usd(current_price)}
📉 *Variación:* {change_pct:+.2f}%

{action_emoji} *ACCIÓN: {label.upper()}*{extra_info}

━━━━━━━━━━━━━━━━━━━━
💼 Capital invertido: {format_usd(capital)}
💰 Valor actual: {format_usd(current_value)}
{'📈' if pnl >= 0 else '📉'} Ganancia/Pérdida: {pnl_str}

🕐 {now}
_Reglas del Padre · Disciplina & largo plazo_
""".strip()
    return msg


def build_summary_message(portfolio: list, prices: dict) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines = [f"📊 *RESUMEN DEL PORTFOLIO*\n🕐 {now}\n━━━━━━━━━━━━━━━━━━━━"]
    total_invested = 0
    total_value = 0

    for asset in portfolio:
        price = prices.get(asset["ticker"])
        if price is None:
            lines.append(f"\n⚠️ *{asset['name']}*: precio no disponible")
            continue
        change_pct = (price - asset["entry_price"]) / asset["entry_price"] * 100
        current_value = asset["units"] * price
        pnl = current_value - asset["capital"]
        total_invested += asset["capital"]
        total_value += current_value
        rule = get_active_rule(change_pct)
        rule_str = f"→ {rule['label']}" if rule else "→ Sin señal"
        emoji = "🟢" if change_pct >= 0 else "🔴"

        lines.append(
            f"\n{emoji} *{asset['name']}* ({asset['ticker']})\n"
            f"   Precio: {format_usd(price)} ({change_pct:+.1f}%)\n"
            f"   Valor: {format_usd(current_value)} | P&L: {'+' if pnl>=0 else ''}{format_usd(pnl)}\n"
            f"   {rule_str}"
        )

    total_pnl = total_value - total_invested
    pct_total = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    lines.append(
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Total invertido: {format_usd(total_invested)}\n"
        f"💰 Valor total: {format_usd(total_value)}\n"
        f"{'📈' if total_pnl>=0 else '📉'} P&L total: {'+' if total_pnl>=0 else ''}{format_usd(total_pnl)} ({pct_total:+.1f}%)"
    )
    return "\n".join(lines)

# ─────────────────────────────────────────
#  LOOP PRINCIPAL
# ─────────────────────────────────────────
async def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)
    log.info("🤖 Bot iniciado. Monitoreando cada %d minutos...", CHECK_INTERVAL_MINUTES)

    # Mensaje de inicio
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"✅ *Portfolio Bot activado*\nMonitoreo cada {CHECK_INTERVAL_MINUTES} minutos.\nUsa /resumen para ver el estado actual.",
        parse_mode=ParseMode.MARKDOWN
    )

    portfolio = load_portfolio()
    check_count = 0

    while True:
        try:
            log.info("🔍 Revisando precios (#%d)...", check_count + 1)
            prices = await fetch_all_prices(portfolio)
            alerts_sent = 0

            for asset in portfolio:
                ticker = asset["ticker"]
                price = prices.get(ticker)

                if price is None:
                    log.warning("No se pudo obtener precio para %s", ticker)
                    continue

                change_pct = (price - asset["entry_price"]) / asset["entry_price"] * 100
                rule = get_active_rule(change_pct)

                log.info(
                    "%s: $%.2f (%.2f%%) — Regla: %s",
                    ticker, price, change_pct,
                    rule["label"] if rule else "Sin señal"
                )

                # Solo alertar si la regla cambió desde la última vez
                rule_key = f"{rule['side']}_{rule['pct']}" if rule else None
                last_key = asset.get("last_alert_rule")

                if rule and rule["action"] in ("BUY", "SELL") and rule_key != last_key:
                    msg = build_alert_message(asset, price, change_pct, rule)
                    await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    asset["last_alert_rule"] = rule_key
                    alerts_sent += 1
                    log.info("📨 Alerta enviada para %s: %s", ticker, rule["label"])
                elif not rule or rule["action"] == "HOLD":
                    # Resetear si el precio volvió a zona neutral
                    if last_key and abs(change_pct) < 8:
                        asset["last_alert_rule"] = None

            save_portfolio(portfolio)

            # Resumen cada 4 horas (16 ciclos de 15 min)
            check_count += 1
            if check_count % 16 == 0:
                summary = build_summary_message(portfolio, prices)
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=summary,
                    parse_mode=ParseMode.MARKDOWN
                )
                log.info("📊 Resumen enviado.")

            if alerts_sent == 0:
                log.info("✅ Sin alertas activas. Próxima revisión en %d min.", CHECK_INTERVAL_MINUTES)

        except Exception as e:
            log.error("❌ Error en el ciclo de monitoreo: %s", e)
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"⚠️ Error en el bot: {e}\nSiguiendo en {CHECK_INTERVAL_MINUTES} min...",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


async def main():
    portfolio = load_portfolio()
    bot = Bot(token=TELEGRAM_TOKEN)

    # Manejador de comandos simple via polling
    import sys
    if "--summary" in sys.argv:
        prices = await fetch_all_prices(portfolio)
        msg = build_summary_message(portfolio, prices)
        print(msg)
        return

    await monitor_loop()


if __name__ == "__main__":
    asyncio.run(main())
