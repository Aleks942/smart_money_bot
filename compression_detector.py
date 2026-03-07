# compression_detector.py

def detect_compression(candles):

    if not candles or len(candles) < 10:
        return False

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    ranges = [h - l for h, l in zip(highs, lows)]

    last_range = ranges[-1]
    avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)

    last_vol = volumes[-1]
    avg_vol = sum(volumes[:-1]) / max(len(volumes[:-1]), 1)

    # диапазон сжимается
    range_compression = last_range < avg_range * 0.7

    # объём растёт
    volume_rising = last_vol > avg_vol * 1.2

    if range_compression and volume_rising:
        return True

    return False
