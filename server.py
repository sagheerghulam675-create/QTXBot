import os
import json
import math
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# Binance public market-data API
BINANCE_URL = (
    "https://data-api.binance.vision/api/v3/klines"
    "?symbol={symbol}&interval=1m&limit=100"
)


def get_candles(symbol):
    url = BINANCE_URL.format(symbol=symbol)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QTXBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    candles = []

    for item in data:
        candles.append({
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5])
        })

    return candles


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

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

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    rsi = calculate_rsi(closes, 14)

    current_price = closes[-1]

    score = 0

    # EMA trend
    if ema9 > ema21:
        score += 2
    elif ema9 < ema21:
        score -= 2

    # Price vs EMA21
    if current_price > ema21:
        score += 1
    elif current_price < ema21:
        score -= 1

    # RSI
    if rsi > 55 and rsi < 75:
        score += 1
    elif rsi < 45 and rsi > 25:
        score -= 1

    # Recent candle momentum
    recent_change = closes[-1] - closes[-6]

    if recent_change > 0:
        score += 1
    elif recent_change < 0:
        score -= 1

    # Decision
    if score >= 3:
        signal = "CALL"
    elif score <= -3:
        signal = "PUT"
    else:
        signal = "WAIT"

    # Confidence
    confidence = 50 + min(abs(score) * 8, 40)

    # WAIT should have lower confidence
    if signal == "WAIT":
        confidence = 50 + min(abs(score) * 5, 15)

    return {
        "signal": signal,
        "confidence": confidence,
        "price": round(current_price, 8),
        "ema9": round(ema9, 8),
        "ema21": round(ema21, 8),
        "rsi": round(rsi, 2),
        "score": score
    }


class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def send_json(self, data, status=200):

        body = json.dumps(data).encode("utf-8")

        try:
            self.send_response(status)

            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )

            self.send_header(
                "Access-Control-Allow-Origin",
                "*"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Connection",
                "close"
            )

            self.send_header("Access-Control-Allow-Origin", "*")
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
                "message": "QTXBot Live Analysis API"
            })

            return


        if self.path.startswith("/api/signal"):

            # Selected crypto pair from the Android app
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query)
            requested_pair = query.get("pair", ["BTC/USDT"])[0].upper()
            requested_expiry = query.get("expiry", ["1 MIN"])[0].upper()

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

            if requested_expiry not in allowed_expiry:
                self.send_json({
                    "error": "Unsupported expiry",
                    "expiry": requested_expiry
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

            symbol = pair_map.get(requested_pair)

            if symbol is None:
                self.send_json({
                    "error": "Unsupported pair",
                    "pair": requested_pair
                }, 400)
                return

            display_pair = requested_pair

            try:

                candles = get_candles(symbol)

                analysis = analyze_market(candles)

                self.send_json({

                    "pair": display_pair,

                    "signal": analysis["signal"],

                    "confidence": analysis["confidence"],

                    "expiry": requested_expiry,
                    "expiry_seconds": allowed_expiry[requested_expiry],

                    "time": datetime.now().strftime("%H:%M:%S"),

                    "price": analysis["price"],

                    "ema9": analysis["ema9"],

                    "ema21": analysis["ema21"],

                    "rsi": analysis["rsi"],

                    "score": analysis["score"],

                    "source": "Live market candles",

                    "analysis": "EMA + RSI + Momentum"

                })

            except Exception as e:

                self.send_json({

                    "signal": "WAIT",

                    "confidence": 0,

                    "error": "Market data unavailable",

                    "details": str(e),

                    "time": datetime.now().strftime("%H:%M:%S")

                }, 503)

            return


        self.send_json({
            "error": "Not Found"
        }, 404)


    def log_message(self, format, *args):

        print(
            "%s - %s"
            % (
                datetime.now().strftime("%H:%M:%S"),
                format % args
            )
        )


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 8080))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    print(
        "QTXBot Live Analysis API is running on port",
        port
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("\nServer stopped")

    finally:

        server.server_close()
