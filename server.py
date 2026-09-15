import os
import json
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BINANCE_URL = "https://data-api.binance.vision/api/v3/klines?symbol={}&interval=1m&limit=100"

def get_candles(symbol):
    req = urllib.request.Request(
        BINANCE_URL.format(symbol),
        headers={"User-Agent": "QTXBot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())

    if len(data) < 30:
        raise ValueError("Not enough market candles")

    return [
        {
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5])
        }
        for x in data
    ]

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for price in values[period:]:
        result = ((price - result) * multiplier) + result

    return result

def calculate_rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze_market(candles):
    closes = [c["close"] for c in candles]

    price = closes[-1]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi = calculate_rsi(closes, 14)
    momentum = closes[-1] - closes[-6]

    last_open = candles[-1]["open"]
    last_close = candles[-1]["close"]

    score = 0

    if ema9 > ema21:
        score += 2
        trend = "BULLISH"
    elif ema9 < ema21:
        score -= 2
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    if price > ema21:
        score += 1
    elif price < ema21:
        score -= 1

    if 50 < rsi < 68:
        score += 1
    elif 32 < rsi < 50:
        score -= 1

    if momentum > 0:
        score += 1
    elif momentum < 0:
        score -= 1

    if last_close > last_open:
        candle = "BULLISH"
        score += 1
    elif last_close < last_open:
        candle = "BEARISH"
        score -= 1
    else:
        candle = "NEUTRAL"

    strong_call = (
        score >= 4
        and ema9 > ema21
        and price > ema21
        and 50 < rsi < 68
        and momentum > 0
    )

    strong_put = (
        score <= -4
        and ema9 < ema21
        and price < ema21
        and 32 < rsi < 50
        and momentum < 0
    )

    if strong_call:
        signal = "CALL"
    elif strong_put:
        signal = "PUT"
    else:
        signal = "NO SIGNAL"

    if signal in ("CALL", "PUT"):
        confidence = 60 + min((abs(score) - 4) * 5, 20)
    else:
        confidence = 50 + min(abs(score) * 3, 9)

    return {
        "signal": signal,
        "confidence": confidence,
        "price": round(price, 8),
        "ema9": round(ema9, 8),
        "ema21": round(ema21, 8),
        "rsi": round(rsi, 2),
        "momentum": round(momentum, 8),
        "score": score,
        "trend": trend,
        "candle": candle
    }

class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")

        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True

    def do_GET(self):

        if self.path == "/":
            self.send_json({
                "status": "ok",
                "message": "QTXBot Live Analysis API",
                "source": "Binance live market candles"
            })
            return

        if self.path.startswith("/api/signal"):

            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)

            pair = query.get("pair", ["BTC/USDT"])[0].upper()
            expiry = query.get("expiry", ["1 MIN"])[0].upper()

            allowed_expiry = {
                "3 SEC": 3,
                "5 SEC": 5,
                "10 SEC": 10,
                "15 SEC": 15,
                "30 SEC": 30,
                "1 MIN": 60,
                "2 MIN": 120,
                "3 MIN": 180,
                "5 MIN": 300,
                "10 MIN": 600,
                "15 MIN": 900,
                "1 HOUR": 3600
            }

            if expiry not in allowed_expiry:
                self.send_json({
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "error": "Unsupported expiry"
                }, 400)
                return

            pair_map = {
                "BTC/USDT": "BTCUSDT",
                "BTCUSDT": "BTCUSDT",
                "ETH/USDT": "ETHUSDT",
                "ETHUSDT": "ETHUSDT",
                "BNB/USDT": "BNBUSDT",
                "BNBUSDT": "BNBUSDT",
                "SOL/USDT": "SOLUSDT",
                "SOLUSDT": "SOLUSDT",
                "XRP/USDT": "XRPUSDT",
                "XRPUSDT": "XRPUSDT"
            }

            symbol = pair_map.get(pair)

            if symbol is None:
                self.send_json({
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "error": "Unsupported pair",
                    "pair": pair
                }, 400)
                return

            try:
                candles = get_candles(symbol)
                result = analyze_market(candles)

                self.send_json({
                    "pair": pair,
                    "signal": result["signal"],
                    "confidence": result["confidence"],
                    "expiry": expiry,
                    "expiry_seconds": allowed_expiry[expiry],
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "price": result["price"],
                    "ema9": result["ema9"],
                    "ema21": result["ema21"],
                    "rsi": result["rsi"],
                    "momentum": result["momentum"],
                    "score": result["score"],
                    "trend": result["trend"],
                    "candle": result["candle"],
                    "data_verified": True,
                    "source": "Binance live market candles",
                    "analysis": "EMA9 + EMA21 + RSI + Momentum + Trend Filter",
                    "status": "LIVE"
                })

            except Exception as e:
                self.send_json({
                    "pair": pair,
                    "signal": "NO SIGNAL",
                    "confidence": 0,
                    "data_verified": False,
                    "status": "DATA ERROR",
                    "error": "Market data unavailable",
                    "details": str(e),
                    "time": datetime.now().strftime("%H:%M:%S")
                }, 503)

            return

        self.send_json({"error": "Not Found"}, 404)

    def log_message(self, format, *args):
        print(
            "%s - %s" %
            (datetime.now().strftime("%H:%M:%S"), format % args)
        )

port = int(os.environ.get("PORT", 8080))

server = ThreadingHTTPServer(
    ("0.0.0.0", port),
    Handler
)

print("QTXBot Live Analysis API is running on port", port)

server.serve_forever()
