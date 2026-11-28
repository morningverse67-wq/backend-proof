"""049. 캐시 스탬피드: 만료 순간 동시 요청이 DB로 몰리는 수와 락 대기 vs 조기 갱신 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 42
WINDOW_MS = 1_000          # 만료 시각(0 ms)부터 관찰하는 구간
DB_MS = 50.0               # DB 한 번 조회 시간(가정, 고정)
RATES = (100, 500, 2000)   # 키 하나에 들어오는 읽기 요청(건/초)
EARLY_START_MS = -6_000    # 조기 갱신: TTL 60초의 90% 지점(만료 6초 전)에 첫 요청이 갱신을 시작(가정)


def arrivals(rate, seed=SEED):
    """만료 뒤 WINDOW_MS 안에 도착하는 요청 시각(ms), 포아송 과정."""
    rnd = random.Random(seed)
    t, out = 0.0, []
    while True:
        t += rnd.expovariate(rate / 1000)
        if t >= WINDOW_MS:
            return out
        out.append(t)


def pct(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else 0.0


def no_lock(arr):
    """전략 A: 잠금 없음. 값이 없는 동안 들어온 요청마다 DB를 조회한다."""
    misses = [t for t in arr if t < DB_MS]
    lat = [0.0] * (len(arr) - len(misses)) + [DB_MS] * len(misses)
    return dict(db=len(misses), waiters=0, lat=lat, misses=len(misses))


def single_flight(arr):
    """전략 B: 갱신 락 하나. 첫 요청만 DB를 조회하고, 나머지는 결과를 기다린다."""
    misses = [t for t in arr if t < DB_MS]
    db = 1 if misses else 0
    lat = [0.0] * (len(arr) - len(misses)) + [DB_MS - t for t in misses]
    return dict(db=db, waiters=max(len(misses) - 1, 0), lat=lat, misses=len(misses))


def early_refresh(arr):
    """전략 C: TTL 90% 지점에서 갱신 시작. 만료 순간에는 값이 이미 있어 미스가 없다. 갱신 조회 1번은 만료 전에 나간다."""
    lat = [0.0] * len(arr)
    return dict(db=0, refresh=1, waiters=0, lat=lat, misses=0)


def run(rate):
    arr = arrivals(rate)
    a, b, c = no_lock(arr), single_flight(arr), early_refresh(arr)
    assert len(arr) > 0
    assert a["db"] >= b["db"] >= c["db"]          # 잠금이 DB 조회를 줄인다
    assert b["db"] <= 1 and c["misses"] == 0
    return dict(n=len(arr), A=a, B=b, C=c)


def experiment():
    print("| 읽기 요청/초 | 전략 | 만료 구간 요청 | DB 조회 | 락 대기 요청 | 요청 지연 p99 (ms) |")
    print("|---:|---|---:|---:|---:|---:|")
    res = {}
    for rate in RATES:
        r = run(rate)
        res[rate] = r
        for key, name in (("A", "잠금 없음"), ("B", "단일 갱신 락"), ("C", "조기 갱신")):
            v = r[key]
            extra = f" (+갱신 {v['refresh']})" if "refresh" in v else ""
            print(f"| {rate:,} | {name} | {r['n']:,} | {v['db']:,}{extra} | {v['waiters']:,} | {pct(v['lat'], 0.99):.1f} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    top_rate = RATES[-1]
    rows = [("잠금 없음", res[top_rate]["A"]), ("단일 갱신 락", res[top_rate]["B"]), ("조기 갱신", res[top_rate]["C"])]
    top = max(v["db"] for _, v in rows)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">DB queries at cache expiry per strategy</title>',
         '<desc id="d">' + "; ".join(f'{n}: {v["db"]} DB queries, {v["waiters"]} waiters' for n, v in rows) + f'. {top_rate} reads per second.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         '<text x="28" y="40" font-size="22" font-weight="bold">Cache stampede at expiry: DB queries (2,000 reads/s)</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw, bw = pw / len(rows), 150
    for i, (name, v) in enumerate(rows):
        x = x0 + i * gw + (gw - bw) / 2
        bh = max(v["db"] / top * ph, 2)
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{bh:.1f}" fill="#2563eb"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["db"]:,} queries</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{name}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 40}" font-size="12" text-anchor="middle">waiters: {v["waiters"]:,}</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">DB query 50 ms (assumed), 1 s window after expiry, early refresh at TTL 90% (assumed).</text>')
    s.append(f'<text x="28" y="{h - 14}" font-size="13">Python {platform.python_version()}, seeded simulation (seed {SEED}).</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    args = ap.parse_args()
    res = experiment()
    if args.write_svg:
        write_svg(res)
