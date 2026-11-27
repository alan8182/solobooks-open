import os
import base64
import subprocess

workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logo_path = os.path.join(workspace, 'brand/solo-logo-256.png')


def get_b64(p: str) -> str:
    if os.path.exists(p):
        with open(p, 'rb') as f:
            return 'data:image/png;base64,' + base64.b64encode(f.read()).decode('utf-8')
    return ''


logo_b64 = get_b64(logo_path)

# Strict DESIGN.md palette: #F6F7F9 page, #FFFFFF surface, #DDE3EA border,
# #182230/#586474 text, #1747B0 primary blue, #0F7354/#EAF7F1 single verification accent.
html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>梭罗记账 SoloBooks —— 不做糊涂账，合规用梭罗</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: "Inter", "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      width: 1920px;
      height: 1080px;
      overflow: hidden;
      background-color: #F6F7F9;
      background-image:
        radial-gradient(circle at 82% 20%, rgba(23, 71, 176, 0.06) 0%, transparent 48%),
        radial-gradient(circle at 1px 1px, rgba(24, 34, 48, 0.05) 1px, transparent 0);
      background-size: 100% 100%, 40px 40px;
      color: #182230;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 120px;
      -webkit-font-smoothing: antialiased;
    }}

    .left-col {{
      width: 820px;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }}

    .brand-lockup {{
      display: flex;
      align-items: center;
      gap: 18px;
      margin-bottom: 40px;
    }}
    .brand-logo {{
      width: 68px;
      height: 68px;
      border-radius: 16px;
      border: 1px solid #DDE3EA;
      box-shadow: 0 8px 20px rgba(23, 71, 176, 0.12);
    }}
    .brand-name-row {{
      display: flex;
      align-items: baseline;
      gap: 12px;
    }}
    .brand-cn {{
      font-size: 32px;
      font-weight: 700;
      color: #182230;
      letter-spacing: -0.02em;
      line-height: 1.1;
    }}
    .brand-en {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.14em;
      color: #1747B0;
      text-transform: uppercase;
    }}
    .brand-meta {{
      margin-top: 6px;
      font-size: 15px;
      font-weight: 500;
      color: #586474;
    }}

    .headline {{
      font-size: 72px;
      font-weight: 800;
      line-height: 1.16;
      letter-spacing: -0.03em;
      color: #182230;
      margin-bottom: 26px;
    }}
    .headline-accent {{
      color: #1747B0;
    }}

    .subtitle {{
      font-size: 24px;
      font-weight: 400;
      line-height: 1.55;
      color: #586474;
      max-width: 680px;
      margin-bottom: 44px;
    }}

    .specs-row {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .spec-pill {{
      padding: 11px 18px;
      background: #FFFFFF;
      border: 1px solid #DDE3EA;
      border-radius: 10px;
      font-size: 16px;
      font-weight: 600;
      color: #182230;
      box-shadow: 0 1px 3px rgba(24, 34, 48, 0.04);
    }}

    .right-col {{
      width: 740px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}

    .focus-card {{
      width: 100%;
      background: #FFFFFF;
      border: 1px solid #DDE3EA;
      border-radius: 20px;
      padding: 40px;
      box-shadow: 0 24px 60px -16px rgba(24, 34, 48, 0.10);
    }}

    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 28px;
    }}
    .card-title-group {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .card-kicker {{
      font-size: 13px;
      font-weight: 600;
      color: #1747B0;
      letter-spacing: 0.04em;
    }}
    .card-title {{
      font-size: 22px;
      font-weight: 700;
      color: #182230;
    }}
    .verified-badge {{
      padding: 8px 14px;
      border-radius: 999px;
      background: #EAF7F1;
      color: #0F7354;
      font-size: 14px;
      font-weight: 600;
      white-space: nowrap;
    }}

    .tx-box {{
      background: #F6F7F9;
      border: 1px solid #EDF0F4;
      border-radius: 14px;
      padding: 26px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
    }}
    .tx-name {{
      font-size: 21px;
      font-weight: 700;
      color: #182230;
    }}
    .tx-sub {{
      margin-top: 6px;
      font-size: 15px;
      color: #586474;
    }}
    .tx-right {{
      text-align: right;
    }}
    .tx-amount {{
      font-size: 28px;
      font-weight: 700;
      color: #182230;
      font-variant-numeric: tabular-nums;
    }}
    .tx-account {{
      margin-top: 6px;
      font-size: 14px;
      font-weight: 600;
      color: #1747B0;
    }}

    .meta-strip {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 28px;
    }}
    .meta-item {{
      padding: 16px 18px;
      border-radius: 12px;
      border: 1px solid #EDF0F4;
      background: #FFFFFF;
    }}
    .meta-label {{
      font-size: 13px;
      font-weight: 600;
      color: #636E7C;
      margin-bottom: 4px;
    }}
    .meta-val {{
      font-size: 15px;
      font-weight: 600;
      color: #182230;
      line-height: 1.4;
    }}

    .cta-bar {{
      width: 100%;
      padding: 18px 26px;
      border-radius: 12px;
      background: #1747B0;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 18px;
      font-weight: 600;
      box-shadow: 0 10px 24px -6px rgba(23, 71, 176, 0.32);
    }}
    .cta-chip {{
      font-size: 14px;
      font-weight: 600;
      padding: 6px 12px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.16);
    }}

    .card-foot {{
      margin-top: 16px;
      text-align: center;
      font-size: 14px;
      font-weight: 500;
      color: #636E7C;
    }}
  </style>
</head>
<body>

  <div class="left-col">
    <div class="brand-lockup">
      <img src="{logo_b64}" alt="SoloBooks" class="brand-logo">
      <div>
        <div class="brand-name-row">
          <span class="brand-cn">梭罗记账</span>
          <span class="brand-en">SoloBooks</span>
        </div>
        <div class="brand-meta">面向小微企业的确定性 AI 财务底座 · 芥子星辰出品</div>
      </div>
    </div>

    <h1 class="headline">
      不做糊涂账，<br>
      <span class="headline-accent">合规用梭罗。</span>
    </h1>

    <p class="subtitle">
      AI 接管机械对账，用确定性会计内核守住人机双签。
    </p>

    <div class="specs-row">
      <span class="spec-pill">《小企业会计准则》</span>
      <span class="spec-pill">27 个原生 MCP</span>
      <span class="spec-pill">开源可自托管</span>
    </div>
  </div>

  <div class="right-col">
    <div class="focus-card">
      <div class="card-header">
        <div class="card-title-group">
          <span class="card-kicker">AI 起草 · 人工确认</span>
          <div class="card-title">待确认入账凭证</div>
        </div>
        <span class="verified-badge">✓ 借贷已平 · 票流一致</span>
      </div>

      <div class="tx-box">
        <div>
          <div class="tx-name">腾讯云算力与云主机服务</div>
          <div class="tx-sub">企银流水自动认领 · 已匹配全电专票</div>
        </div>
        <div class="tx-right">
          <div class="tx-amount">-¥ 2,800.00</div>
          <div class="tx-account">4301 研发支出 / 应交税费</div>
        </div>
      </div>

      <div class="meta-strip">
        <div class="meta-item">
          <div class="meta-label">规则依据</div>
          <div class="meta-val">命中企业专属分类记忆规则</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">合规边界</div>
          <div class="meta-val">AI 不直接落账 · 确认后生成三表</div>
        </div>
      </div>

      <div class="cta-bar">
        <span>确认入账并同步三表</span>
        <span class="cta-chip">人机双签 ✓</span>
      </div>

      <div class="card-foot">
        严格借贷平衡 · 账套数据随时完整导出
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
    f'file://{output_html}',
]

res = subprocess.run(cmd, capture_output=True, text=True, check=True)
print('Updated screenshot generated at:', output_png)
