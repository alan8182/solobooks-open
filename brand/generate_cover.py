import os
import base64
import subprocess

workspace = '/Users/ecarx/Documents/solobooks'
logo_path = os.path.join(workspace, 'brand/solo-logo-256.png')

def get_b64(p):
    if os.path.exists(p):
        with open(p, 'rb') as f:
            return 'data:image/png;base64,' + base64.b64encode(f.read()).decode('utf-8')
    return ''

logo_b64 = get_b64(logo_path)

html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>SoloBooks 16:9 Cover - 20min & Clarity</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Segoe UI", sans-serif;
      margin: 0;
      padding: 0;
      width: 1920px;
      height: 1080px;
      overflow: hidden;
      background-color: #F8FAFC;
      -webkit-font-smoothing: antialiased;
    }}
    .bg-grid {{
      background-image: radial-gradient(circle at 1px 1px, #CBD5E1 1.2px, transparent 0);
      background-size: 36px 36px;
    }}
    .glow-top-right {{
      position: absolute;
      width: 900px;
      height: 900px;
      right: -100px;
      top: -150px;
      background: radial-gradient(circle, rgba(37, 99, 235, 0.14) 0%, rgba(59, 130, 246, 0.04) 50%, transparent 70%);
      pointer-events: none;
    }}
    .glow-bottom-left {{
      position: absolute;
      width: 800px;
      height: 800px;
      left: -150px;
      bottom: -150px;
      background: radial-gradient(circle, rgba(99, 102, 241, 0.08) 0%, transparent 70%);
      pointer-events: none;
    }}
  </style>
</head>
<body class="relative flex items-center justify-between px-24 bg-slate-50 bg-grid">

  <!-- Ambient Glows -->
  <div class="glow-top-right"></div>
  <div class="glow-bottom-left"></div>

  <!-- Left Column: Core Brand & 20-Minute Hook -->
  <div class="w-[830px] flex flex-col justify-center z-10 py-10">
    
    <!-- Brand Lockup -->
    <div class="flex items-center gap-4 mb-6">
      <img src="{logo_b64}" alt="SoloBooks" class="w-16 h-16 rounded-2xl shadow-lg ring-1 ring-slate-900/10">
      <div>
        <div class="flex items-center gap-3">
          <span class="text-[34px] font-black tracking-tight text-slate-900 leading-none">梭罗记账</span>
          <span class="text-2xl font-black tracking-widest text-blue-600 uppercase leading-none">SoloBooks</span>
        </div>
        <div class="text-[13px] font-bold tracking-wider text-slate-500 uppercase mt-1.5 flex items-center gap-2">
          <span>驻场 AI 财务官 (AI CFO)</span>
          <span class="text-slate-300">·</span>
          <span class="text-blue-600 font-extrabold">面向一人公司与小微团队</span>
        </div>
      </div>
    </div>

    <!-- Highlight Hook Pill (20 Minutes & Total Clarity) -->
    <div class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-[14px] font-extrabold bg-amber-50 border border-amber-300/80 text-amber-900 shadow-xs w-fit mb-5">
      <span class="flex h-2 w-2 relative">
        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
        <span class="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
      </span>
      <span>⏱️ 每月仅需 20 分钟搞定 · 财务底牌与真实利润轻松全掌握</span>
    </div>

    <!-- H1 Headline (Huge, Confident, Clean) -->
    <h1 class="text-[64px] font-black tracking-tight leading-[1.14] text-slate-900 mb-6">
      点一点把账做清爽，<br>
      <span class="bg-gradient-to-r from-blue-700 via-indigo-600 to-blue-500 bg-clip-text text-transparent">问一问把公司养值钱。</span>
    </h1>

    <!-- Subtitle -->
    <p class="text-[21px] text-slate-600 leading-relaxed font-normal mb-9 max-w-[730px]">
      不当甩手掌柜，也不做繁重会计。<strong class="text-slate-900 font-bold">每月花 20 分钟轻拍板</strong>，告别糊涂代账与公私混同雷区，随时看清现金流与真实利润，养出一本接得住尽调的干净账本。
    </p>

    <!-- Bottom Feature Pills -->
    <div class="flex items-center gap-3">
      <div class="px-4 py-2.5 rounded-xl bg-slate-900 text-white text-sm font-bold flex items-center gap-2 shadow-sm">
        <span>🐳</span> Docker 免费自托管
      </div>
      <div class="px-4 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-bold flex items-center gap-2 shadow-sm">
        <span>🔌</span> 24项原生 MCP 账务工具
      </div>
      <div class="px-4 py-2.5 rounded-xl bg-white border border-slate-300 text-slate-800 text-sm font-bold flex items-center gap-2 shadow-sm">
        <span>🛡️</span> 公私合规隔离
      </div>
      <div class="px-4 py-2.5 rounded-xl bg-emerald-50 border border-emerald-300 text-emerald-800 text-sm font-bold flex items-center gap-2 shadow-sm">
        <span>📑</span> 98分 尽调级账本
      </div>
    </div>

  </div>

  <!-- Right Column: Two Crisp, Standalone High-Impact Cards -->
  <div class="w-[740px] flex flex-col gap-6 z-10 justify-center">

    <!-- Card 1: 「点一点」智能入账 -->
    <div class="bg-white rounded-3xl p-7 shadow-[0_20px_50px_rgba(23,71,176,0.12)] border border-slate-200/90 relative overflow-hidden">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <span class="w-8 h-8 rounded-xl bg-blue-100 flex items-center justify-center text-blue-700 font-black text-base">⚡️</span>
          <span class="text-lg font-black text-slate-900 tracking-tight">点一点 · 20分钟搞定月度账务</span>
        </div>
        <span class="text-xs font-bold bg-blue-50 text-blue-700 border border-blue-200 px-3 py-1 rounded-full">
          微信 / 银行流水自动识别
        </span>
      </div>

      <!-- Transaction Preview -->
      <div class="bg-slate-50 rounded-2xl p-4 border border-slate-200/70 mb-4 flex items-center justify-between">
        <div>
          <div class="font-bold text-slate-900 text-base">腾讯云 GPU 算力与模型租赁</div>
          <div class="text-xs text-slate-500 mt-0.5">已自动匹配专票 · 进项税额已核销</div>
        </div>
        <div class="text-right">
          <div class="text-xl font-black text-slate-900">-¥ 2,800.00</div>
          <div class="text-xs text-blue-600 font-bold">4301 研发支出(加计扣除)</div>
        </div>
      </div>

      <!-- Action Button -->
      <div class="w-full py-3.5 px-6 bg-gradient-to-r from-amber-400 via-amber-500 to-amber-600 text-slate-950 font-black text-base rounded-2xl shadow-lg shadow-amber-500/25 flex items-center justify-between border border-amber-300">
        <span class="flex items-center gap-2">
          <span>✨</span> AI 已预制最优方案，点击采纳
        </span>
        <span class="bg-amber-200/80 text-amber-950 text-xs px-3 py-1 rounded-lg font-black uppercase">
          点一下即入账 ✓
        </span>
      </div>
      <div class="text-center text-xs text-slate-400 mt-2 font-medium">零会计借贷门槛 · 凭证与报表全自动生成</div>
    </div>

    <!-- Card 2: 「问一问」7x24h 战略财税军师 & 财务底牌全掌握 -->
    <div class="bg-white rounded-3xl p-7 shadow-[0_20px_50px_rgba(79,70,229,0.12)] border border-slate-200/90 relative overflow-hidden">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <span class="w-8 h-8 rounded-xl bg-indigo-100 flex items-center justify-center text-indigo-700 font-black text-base">💬</span>
          <span class="text-lg font-black text-slate-900 tracking-tight">问一问 · 轻松掌控公司财务底牌</span>
        </div>
        <span class="text-xs font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 px-3 py-1 rounded-full">
          金税四期避坑指南
        </span>
      </div>

      <!-- Dialogue Block -->
      <div class="space-y-3 mb-4">
        <!-- User Question -->
        <div class="bg-slate-100 rounded-2xl rounded-tl-sm p-3.5 text-sm font-medium text-slate-800 flex items-start gap-2.5">
          <span class="text-slate-400 text-xs font-bold shrink-0 mt-0.5">老板提问</span>
          <span>“我用个人卡替公司付了 3.8 万采购，怎么合规转回给我？”</span>
        </div>

        <!-- AI CFO Answer -->
        <div class="bg-indigo-50/80 border border-indigo-100 rounded-2xl rounded-tr-sm p-3.5 text-sm text-indigo-950 flex items-start gap-2.5">
          <span class="text-indigo-600 text-xs font-bold shrink-0 mt-0.5">AI 财务官</span>
          <span class="leading-relaxed">已自动生成<strong>《免税垫资冲销单》</strong>并核验发票。直接公转私转回，公私分明，<strong>彻底杜绝 20% 股东分红个税风险</strong>。</span>
        </div>
      </div>

      <!-- Quick Action / Status -->
      <div class="flex items-center justify-between pt-1">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold bg-indigo-50 text-indigo-700 border border-indigo-200 px-3 py-1 rounded-lg flex items-center gap-1">
            <span>⚡️</span> 一键导出垫资冲销底稿
          </span>
          <span class="text-xs font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 px-3 py-1 rounded-lg flex items-center gap-1">
            <span>🛡️</span> 0 违规挂账
          </span>
        </div>
        <span class="text-xs text-slate-400 font-medium">结合企业真实账本解答</span>
      </div>
    </div>

  </div>

</body>
</html>
'''

output_html = os.path.join(workspace, 'brand/cover-16-9.html')
with open(output_html, 'w', encoding='utf-8') as f:
    f.write(html_content)

output_png = os.path.join(workspace, 'brand/cover-16-9.png')
cmd = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '--headless',
    '--disable-gpu',
    f'--screenshot={output_png}',
    '--window-size=1920,1080',
    '--hide-scrollbars',
    f'file://{output_html}'
]

subprocess.run(cmd, capture_output=True, text=True)
print("Updated screenshot generated at:", output_png)
