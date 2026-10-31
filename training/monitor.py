"""Watch a training run in the browser: loss, accuracy, speed and time left.

    python training/monitor.py tmp/finetune/run2 --steps 30000 --port 8770
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>Training monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#fff; --ink:#1f1f1d; --muted:#6f6e69; --line:#e4e2da; --series:#2a6fb0; --card:#faf9f5; --good:#2e7d32; }
@media (prefers-color-scheme: dark) { :root { --bg:#1f1f1d; --ink:#ecebe6; --muted:#a3a29b; --line:#3a3a36; --series:#6aa9e0; --card:#2a2a27; --good:#7cc47f; } }
* { box-sizing:border-box; } body { margin:0; padding:20px; background:var(--bg); color:var(--ink); font:14px/1.5 system-ui,"Microsoft JhengHei",sans-serif; }
h1 { font-size:18px; font-weight:600; margin:0 0 4px; } .sub { color:var(--muted); margin-bottom:16px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; } .tile b { display:block; font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; } .tile span { color:var(--muted); font-size:12px; }
.bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-bottom:16px; } .bar i { display:block; height:100%; background:var(--series); }
.charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }
.chart { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; position:relative; } .chart h2 { font-size:13px; font-weight:600; margin:0 0 8px; }
svg { width:100%; height:220px; display:block; } text { fill:var(--muted); font-size:11px; } .grid { stroke:var(--line); stroke-width:1; } .ln { fill:none; stroke:var(--series); stroke-width:2; } .dot { fill:var(--series); stroke:var(--card); stroke-width:2; }
.tip { position:absolute; pointer-events:none; background:var(--ink); color:var(--bg); padding:3px 7px; border-radius:4px; font-size:12px; display:none; white-space:nowrap; }
table { width:100%; border-collapse:collapse; margin-top:16px; font-variant-numeric:tabular-nums; } th,td { text-align:right; padding:5px 8px; border-bottom:1px solid var(--line); } th { color:var(--muted); font-weight:500; }
.state { color:var(--good); font-weight:600; }
</style></head><body>
<h1>帳單模型訓練</h1><div class="sub" id="sub">讀取中…</div>
<div class="bar"><i id="bar" style="width:0"></i></div>
<div class="tiles" id="tiles"></div>
<div class="charts">
  <div class="chart"><h2>訓練損失 (loss，越低越好)</h2><svg id="loss" viewBox="0 0 480 220" preserveAspectRatio="none"></svg><div class="tip"></div></div>
  <div class="chart"><h2>合成驗證集正確率 (每 5,000 步，整行全對才算)</h2><svg id="acc" viewBox="0 0 480 220" preserveAspectRatio="none"></svg><div class="tip"></div></div>
</div>
<table id="rows"></table>
<script>
const fmt = (n, d=0) => n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US", {maximumFractionDigits:d, minimumFractionDigits:d});
function chart(id, points, opts) {
  const svg = document.getElementById(id), W=480, H=220, L=46, R=10, T=8, B=24;
  if (!points.length) { svg.innerHTML = '<text x="240" y="110" text-anchor="middle">還沒有資料</text>'; return; }
  const xs = points.map(p=>p[0]), ys = points.map(p=>p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs, x0+1);
  let y0 = opts.min ?? Math.min(...ys), y1 = opts.max ?? Math.max(...ys);
  if (y1 === y0) y1 = y0 + 1;
  const X = x => L + (x-x0)/(x1-x0)*(W-L-R), Y = y => H-B - (y-y0)/(y1-y0)*(H-T-B);
  let g = "";
  for (let i=0;i<=4;i++) { const v=y0+(y1-y0)*i/4, y=Y(v); g += `<line class="grid" x1="${L}" x2="${W-R}" y1="${y}" y2="${y}"/><text x="${L-6}" y="${y+4}" text-anchor="end">${opts.tick(v)}</text>`; }
  for (let i=0;i<=4;i++) { const v=x0+(x1-x0)*i/4; g += `<text x="${X(v)}" y="${H-6}" text-anchor="middle">${fmt(v)}</text>`; }
  const path = points.map((p,i)=>(i?"L":"M")+X(p[0]).toFixed(1)+" "+Y(p[1]).toFixed(1)).join(" ");
  const last = points[points.length-1];
  svg.innerHTML = g + `<path class="ln" d="${path}"/><circle class="dot" r="4" cx="${X(last[0])}" cy="${Y(last[1])}"/><rect id="${id}-hit" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/>`;
  const tip = svg.parentElement.querySelector(".tip");
  const hit = svg.querySelector("rect");
  hit.onmousemove = e => {
    const box = svg.getBoundingClientRect(), mx = (e.clientX-box.left)/box.width*W;
    let best = points[0]; for (const p of points) if (Math.abs(X(p[0])-mx) < Math.abs(X(best[0])-mx)) best = p;
    tip.style.display="block"; tip.textContent = `第 ${fmt(best[0])} 步：${opts.tick(best[1])}`;
    tip.style.left = Math.min(box.width-120, X(best[0])/W*box.width+14)+"px"; tip.style.top = (Y(best[1])/H*box.height+20)+"px";
  };
  hit.onmouseleave = () => tip.style.display="none";
}
async function refresh() {
  let d; try { d = await (await fetch("/data",{cache:"no-store"})).json(); } catch { document.getElementById("sub").textContent="連不上監看程式"; return; }
  const pct = d.step/d.total*100;
  document.getElementById("bar").style.width = pct+"%";
  document.getElementById("sub").innerHTML = (d.running ? '<span class="state">訓練中</span>' : "訓練沒有在執行") + ` · 每 5 秒更新 · ${d.run}`;
  const tiles = [["進度", `${fmt(d.step)} / ${fmt(d.total)}`, `${pct.toFixed(1)}%`],["目前 loss", fmt(d.loss,4), "最近 500 步平均"],["最佳正確率", d.best===null?"—":(d.best*100).toFixed(2)+"%", "合成驗證集"],["速度", fmt(d.speed,1)+" 步/秒", ""],["剩餘時間", d.eta===null?"—":`約 ${fmt(d.eta)} 分鐘`, ""],["GPU", d.gpu ?? "—", "使用率 / 記憶體"]];
  document.getElementById("tiles").innerHTML = tiles.map(t=>`<div class="tile"><span>${t[0]}</span><b>${t[1]}</b><span>${t[2]}</span></div>`).join("");
  chart("loss", d.losses, {tick:v=>v.toFixed(3), min:0});
  chart("acc", d.checks.map(c=>[c.step,c.synthetic_accuracy]), {tick:v=>(v*100).toFixed(1)+"%", max:1});
  document.getElementById("rows").innerHTML = d.checks.length ? "<tr><th>步數</th><th>正確率</th><th>累計分鐘</th></tr>" + d.checks.slice().reverse().map(c=>`<tr><td>${fmt(c.step)}</td><td>${(c.synthetic_accuracy*100).toFixed(2)}%</td><td>${fmt(c.minutes,1)}</td></tr>`).join("") : "";
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""

STEP = re.compile(r"^step (\d+) loss ([\d.]+) ([\d.]+) it/s")


def snapshot(run: Path, total: int) -> dict:
    losses, speed = [], None
    log = run / "train.log"
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = STEP.match(line)
            if match:
                losses.append([int(match[1]), float(match[2])])
                speed = float(match[3])
    checks = []
    results = run / "log.jsonl"
    if results.exists():
        checks = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
    step = losses[-1][0] if losses else 0
    gpu, running = None, False
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        use, memory = [part.strip() for part in out.split(",")]
        gpu, running = f"{use}% / {int(memory) / 1024:.1f} GB", int(memory) > 500
    except Exception:  # noqa: BLE001 - the page works without it
        pass
    return {
        "run": run.name, "step": step, "total": total, "losses": losses, "checks": checks, "speed": speed, "gpu": gpu, "running": running,
        "loss": losses[-1][1] if losses else None,
        "best": max((check["synthetic_accuracy"] for check in checks), default=None),
        "eta": (total - step) / speed / 60 if speed and step < total else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--steps", type=int, default=30000)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body, kind = (json.dumps(snapshot(args.run, args.steps)).encode(), "application/json") if self.path == "/data" else (PAGE.encode(), "text/html; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
