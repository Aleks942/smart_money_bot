import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

MESSAGE_MODE = (os.getenv("MESSAGE_MODE") or "AUTO").upper()
EDGE_MID_SCORE = int(os.getenv("EDGE_MID_SCORE") or "4")
EDGE_HIGH_SCORE = int(os.getenv("EDGE_HIGH_SCORE") or "7")

POLL_SECONDS = 600
STATE_FILE = "state.json"
TIMEOUT = 12

OKX_INST = "BTC-USDT"

# ================= TELEGRAM =================
def send_telegram(text):
    try:
        url=f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":CHAT_ID,"text":text},timeout=TIMEOUT)
    except:
        pass

# ================= STATE =================
def load_state():
    try:
        with open(STATE_FILE,"r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    try:
        with open(STATE_FILE,"w") as f:
            json.dump(state,f)
    except:
        pass

# ================= OKX CANDLES =================
def get_okx_candles(bar="5m",limit=120):
    url="https://www.okx.com/api/v5/market/candles"
    r=requests.get(url,params={"instId":OKX_INST,"bar":bar,"limit":str(limit)},timeout=TIMEOUT)
    data=r.json()
    arr=data["data"]
    arr.reverse()
    candles=[]
    for c in arr:
        candles.append([int(c[0]),float(c[1]),float(c[2]),float(c[3]),float(c[4]),float(c[5])])
    return candles

# ================= DETECTORS =================
def compression_ok(candles):
    if len(candles)<20: return False
    ranges=[c[2]-c[3] for c in candles]
    last=sum(ranges[-4:])/4
    prev=sum(ranges[-12:-4])/8
    return last < prev*0.7

def volume_spike_ok(candles):
    vols=[c[5] for c in candles if c[5] is not None]
    if len(vols)<20: return False
    return vols[-1] > (sum(vols[-21:-1])/20)*1.8

def fake_dump_ok(candles):
    o=candles[-1][1];h=candles[-1][2];l=candles[-1][3];c=candles[-1][4]
    body=abs(c-o)
    lower=min(o,c)-l
    return lower>body*1.8

def breakout_ok(candles,lookback=12):
    highs=[c[2] for c in candles[-lookback-1:-1]]
    lows=[c[3] for c in candles[-lookback-1:-1]]
    close=candles[-1][4]
    if close>max(highs): return "UP"
    if close<min(lows): return "DOWN"
    return None

def breakout_confirm_ok(candles,lookback=12):
    base=candles[-lookback-3:-3]
    hi=max(x[2] for x in base)
    lo=min(x[3] for x in base)
    closes=[c[4] for c in candles[-2:]]
    if all(cl>hi for cl in closes): return "UP"
    if all(cl<lo for cl in closes): return "DOWN"
    return None

def atr_expansion_ok(candles):
    trs=[]
    for i in range(1,len(candles)):
        h=candles[i][2];l=candles[i][3];pc=candles[i-1][4]
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    if len(trs)<20: return False
    now=sum(trs[-14:])/14
    prev=sum(trs[-19:-5])/14
    return now>prev*1.3

def liquidity_pressure(candles):
    seg=candles[-21:-1]
    hi=max(x[2] for x in seg)
    lo=min(x[3] for x in seg)
    close=candles[-1][4]
    pos=(close-lo)/(hi-lo) if hi-lo>0 else 0.5
    if pos>0.85: return "UP",{"hi":hi,"lo":lo}
    if pos<0.15: return "DOWN",{"hi":hi,"lo":lo}
    return None,{"hi":hi,"lo":lo}

# ================= DIRECTION =================
def direction_hint(flags):
    up=0;down=0;reasons=[]
    if "BREAKOUT_CONFIRM_UP" in flags: up+=3;reasons.append("Confirm UP")
    if "BREAKOUT_CONFIRM_DOWN" in flags: down+=3;reasons.append("Confirm DOWN")
    if "BREAKOUT_UP" in flags: up+=2
    if "BREAKOUT_DOWN" in flags: down+=2
    if "PRESSURE_UP" in flags: up+=1
    if "PRESSURE_DOWN" in flags: down+=1
    if "FAKE_DUMP" in flags: up+=1
    if up>=down+2: return "⬆️ Вероятнее ВВЕРХ",reasons,up,down
    if down>=up+2: return "⬇️ Вероятнее ВНИЗ",reasons,up,down
    return "⚖️ Баланс / нет явного направления",reasons,up,down

# ================= ENTRY ENGINE =================
def entry_engine(sig):
    flags=sig["flags"];score=sig["score"]
    up=sig["dir_up"];down=sig["dir_down"]
    if "Баланс" in sig["direction_hint"]:
        return "🔴 WAIT","Нет направления"
    if score>=EDGE_HIGH_SCORE and ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and "ATR_EXPANSION" in flags:
        return "🟢 SAFE ENTRY","Структура подтверждена"
    if score>=EDGE_MID_SCORE:
        return "🟡 AGGRESSIVE ENTRY","Ранний сигнал"
    return "🔴 WAIT","Недостаточно факторов"

# ================= SMART MONEY STAGE =================
def smart_money_stage(sig):
    flags=sig["flags"];score=sig["score"]
    if score<2: return "⚪ NEUTRAL","Нет структуры"
    if ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and "ATR_EXPANSION" in flags:
        return "🟢 EXPANSION","Идёт движение"
    if "FAKE_DUMP" in flags or ("PRESSURE_DOWN" in flags and "BREAKOUT_DOWN" in flags) or ("PRESSURE_UP" in flags and "BREAKOUT_UP" in flags):
        return "🟡 MANIPULATION","Сбор ликвидности"
    if "COMP_5M" in flags or "COMP_15M" in flags:
        return "🟣 ACCUMULATION","Накопление"
    return "⚪ NEUTRAL","Нет явной стадии"

# ================= LIQUIDITY TARGET =================
def liquidity_target(meta,flags):
    if not meta: return None
    hi=meta.get("hi");lo=meta.get("lo")
    if hi is None or lo is None: return None
    if "BREAKOUT_UP" in flags or "PRESSURE_UP" in flags:
        return f"🎯 Цель ликвидности: {hi:.2f}"
    if "BREAKOUT_DOWN" in flags or "PRESSURE_DOWN" in flags:
        return f"🎯 Цель ликвидности: {lo:.2f}"
    return None

# ================= BUILD SIGNAL =================
def build_signal():
    c5=get_okx_candles("5m")
    c15=get_okx_candles("15m")

    price=c5[-1][4]
    flags=[];score=0

    if compression_ok(c5): flags.append("COMP_5M");score+=1
    if compression_ok(c15): flags.append("COMP_15M");score+=1
    if fake_dump_ok(c5): flags.append("FAKE_DUMP");score+=1
    if volume_spike_ok(c5): flags.append("VOL_SPIKE");score+=1

    br=breakout_ok(c5)
    if br: flags.append(f"BREAKOUT_{br}");score+=1

    conf=breakout_confirm_ok(c5)
    if conf: flags.append(f"BREAKOUT_CONFIRM_{conf}");score+=1

    if atr_expansion_ok(c5): flags.append("ATR_EXPANSION");score+=1

    pres,meta=liquidity_pressure(c5)
    if pres: flags.append(f"PRESSURE_{pres}");score+=1

    hint,_,up,down=direction_hint(flags)

    entry,entry_reason=entry_engine({
        "flags":flags,
        "score":score,
        "dir_up":up,
        "dir_down":down,
        "direction_hint":hint
    })

    stage,stage_reason=smart_money_stage({
        "flags":flags,
        "score":score
    })

    target=liquidity_target(meta,flags)

    return {
        "price":price,
        "score":score,
        "flags":flags,
        "hint":hint,
        "dir_up":up,
        "dir_down":down,
        "entry":entry,
        "entry_reason":entry_reason,
        "stage":stage,
        "stage_reason":stage_reason,
        "target":target
    }

# ================= MESSAGES =================
def msg_short(sig):
    lines=[]
    lines.append("🧠 SMART MONEY RADAR — SHORT")
    lines.append(f"💵 BTC: {sig['price']:.2f}")
    lines.append(f"📊 {sig['score']}/10")
    lines.append(f"🎯 {sig['hint']}")
    lines.append(f"🎯 ENTRY: {sig['entry']}")
    lines.append(f"🧬 STAGE: {sig['stage']}")
    if sig["target"]: lines.append(sig["target"])
    return "\n".join(lines)

def msg_medium(sig):
    lines=[]
    lines.append("🧠 SMART MONEY RADAR — MEDIUM")
    lines.append(f"💵 BTC {sig['price']:.2f}")
    lines.append(f"📊 {sig['score']}/10")
    lines.append(f"🎯 {sig['hint']}")
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")
    if sig["target"]: lines.append(sig["target"])
    return "\n".join(lines)

def msg_full(sig):
    lines=[]
    lines.append("🧠 SMART MONEY RADAR — PRO MAX FULL")
    lines.append(f"💵 BTC {sig['price']:.2f}")
    lines.append(f"📊 Score {sig['score']}/10")
    lines.append(f"🎯 {sig['hint']} (up={sig['dir_up']},down={sig['dir_down']})")
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")
    if sig["target"]: lines.append(sig["target"])
    lines.append("Flags:")
    for f in sig["flags"]:
        lines.append(f"• {f}")
    return "\n".join(lines)

def choose_message(sig):
    if MESSAGE_MODE=="SHORT": return msg_short(sig)
    if MESSAGE_MODE=="MEDIUM": return msg_medium(sig)
    if MESSAGE_MODE=="FULL": return msg_full(sig)
    if sig["score"]>=EDGE_HIGH_SCORE:
        return msg_full(sig)
    if sig["score"]>=EDGE_MID_SCORE:
        return msg_medium(sig)
    return msg_short(sig)

# ================= MAIN =================
if __name__=="__main__":
    state=load_state()
    send_telegram("🚀 SMART MONEY RADAR — PRO MAX FINAL started")

    while True:
        try:
            sig=build_signal()

            if sig["score"]!=state.get("prev_score") or sig["flags"]!=state.get("prev_flags"):
                send_telegram(choose_message(sig))

            state["prev_score"]=sig["score"]
            state["prev_flags"]=sig["flags"]
            save_state(state)

        except Exception as e:
            send_telegram(f"❌ Error:\n{str(e)}")

        time.sleep(POLL_SECONDS)
