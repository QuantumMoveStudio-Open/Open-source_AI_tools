import os
import re
import time
import smtplib
import requests
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr


def parse_qp_config(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ 找不到配置文件: {filepath}")
        return None
    except Exception as e:
        print(f"❌ 读取配置文件出错: {e}")
        return None

    pattern = r"'([^']+)'\s*:\s*[\"']([^\"']*)[\"']"
    return dict(re.findall(pattern, content))


def get_api_keys(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ 找不到文件: {filepath}")
        exit(1)
    except Exception as e:
        print(f"❌ 读取文件出错: {e}")
        exit(1)

    match = re.search(r"'apikey'\s*:\s*(.*?);", content, re.S)
    if not match:
        print("❌ 文件里没找到apikey字段！")
        exit(1)

    keys = re.findall(r'"([^"]*)"', match.group(1))
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        print("❌ apikey 字段里没有有效的 key")
        exit(1)
    return keys


def mask_key(k):
    if len(k) <= 10:
        return k[:3] + "****"
    return k[:5] + "****" + k[-4:]


def send_low_balance_email(smtp_cfg, to_email, low_accounts, minimum_balance):
    smtp_server = smtp_cfg.get('smtp_server')
    port        = int(smtp_cfg.get('port', 465))
    from_email  = smtp_cfg.get('email')
    auth_code   = smtp_cfg.get('authorization_code')
    sender_name = smtp_cfg.get('name', 'DeepSeek Balance')

    if not all([smtp_server, from_email, auth_code]):
        print("⚠️ SMTP 配置不完整，跳过发送邮件")
        return False

    subject = f"⚠️ DeepSeek 余额预警：{len(low_accounts)} 个账户低于 ¥{minimum_balance}"

    rows = ""
    for acc in low_accounts:
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border:1px solid #e0e0e0;">账户 {acc['index']}</td>
          <td style="padding:8px 12px;border:1px solid #e0e0e0;font-family:Consolas,monospace;">{acc['masked_key']}</td>
          <td style="padding:8px 12px;border:1px solid #e0e0e0;color:#e74c3c;font-weight:bold;">¥{acc['total_balance']}</td>
        </tr>
        """

    html = f"""
    <html>
      <body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#333;">
        <h2 style="color:#e74c3c;">⚠️ DeepSeek 余额不足提醒</h2>
        <p>以下账户余额已低于预警阈值 <b>¥{minimum_balance}</b>：</p>
        <table style="border-collapse:collapse;margin:12px 0;">
          <thead>
            <tr style="background:#f5f5f5;">
              <th style="padding:8px 12px;border:1px solid #e0e0e0;text-align:left;">账户</th>
              <th style="padding:8px 12px;border:1px solid #e0e0e0;text-align:left;">API Key</th>
              <th style="padding:8px 12px;border:1px solid #e0e0e0;text-align:left;">总余额</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p>请及时充值，避免服务中断。</p>
        <hr style="border:none;border-top:1px solid #eee;">
        <p style="color:#888;font-size:12px;">
          此邮件由 DeepSeek 余额检查脚本自动发送，请勿回复。
        </p>
      </body>
    </html>
    """

    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From']    = formataddr((str(Header(sender_name, 'utf-8')), from_email))
    msg['To']      = to_email

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(smtp_server, port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_server, port, timeout=15)
            server.starttls()
        server.login(from_email, auth_code)
        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()
        print(f"📧 预警邮件已发送至 {to_email}")
        return True
    except Exception as e:
        print(f"❌ 发送邮件失败: {e}")
        return False


STATE_FILE = os.path.join("data", ".low_balance_sent")


def has_sent_flag():
    return os.path.exists(STATE_FILE)


def set_sent_flag():
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            f.write("sent")
    except Exception as e:
        print(f"⚠️ 写入状态文件失败: {e}")


def clear_sent_flag():
    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
        except Exception as e:
            print(f"⚠️ 删除状态文件失败: {e}")


def parse_interval(s):
    try:
        parts = s.strip().split(":")
        if len(parts) != 3:
            return None
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        if h < 0 or m < 0 or sec < 0:
            return None
        total = h * 3600 + m * 60 + sec
        if total <= 0:
            return None
        return total
    except Exception:
        return None


def get_interval():
    config_path = os.path.join("data", "config.qp-cfg")
    cfg = parse_qp_config(config_path)
    if not cfg:
        return None
    raw = cfg.get('testing_time')
    if not raw:
        return None
    return parse_interval(raw)


def query_balance(api_key):
    url = "https://api.deepseek.com/user/balance"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.exceptions.Timeout:
        print("   ❌ 请求超时")
        return None
    except requests.exceptions.ConnectionError:
        print("   ❌ 网络连接失败")
        return None
    except Exception as e:
        print(f"   ❌ 请求异常: {e}")
        return None

    if response.status_code != 200:
        print(f"   ❌ 请求失败，状态码: {response.status_code}")
        print(f"   {response.text}")
        return None

    data = response.json()
    if not data.get("is_available"):
        print("   ❌ 账户状态不可用")
        return None

    balance_info = data["balance_infos"][0]
    return {
        'total_balance': float(balance_info['total_balance']),
        'granted_balance': float(balance_info['granted_balance']),
        'topped_up_balance': float(balance_info['topped_up_balance']),
    }


def check_once():
    file_path = os.path.join("data", "dpsk.qp-apikey")
    api_keys = get_api_keys(file_path)

    config_path = os.path.join("data", "config.qp-cfg")
    cfg = parse_qp_config(config_path)

    minimum_balance = 0
    to_email = None
    if cfg:
        try:
            minimum_balance = float(cfg.get('minimum_balance', 0))
        except ValueError:
            minimum_balance = 0
        to_email = cfg.get('notification_email')

    low_accounts = []

    print(f"🔍 共发现 {len(api_keys)} 个账户，开始查询...")

    for idx, key in enumerate(api_keys, 1):
        print(f"\n 账户 {idx} ({mask_key(key)})")
        result = query_balance(key)
        if result is None:
            continue

        total = result['total_balance']
        print(f"   ✅ 连接成功")
        print(f"   💰 总余额: ¥{total}")
        print(f"   🎁 赠金余额: ¥{result['granted_balance']}")
        print(f"   💳 充值余额: ¥{result['topped_up_balance']}")

        if minimum_balance > 0 and total < minimum_balance:
            low_accounts.append({
                'index': idx,
                'masked_key': mask_key(key),
                'total_balance': total,
            })

    if minimum_balance <= 0:
        print("\n⚠️ minimum_balance 未配置或为 0，跳过余额预警")
        return

    if not low_accounts:
        print(f"\n✅ 所有账户余额均充足")
        if has_sent_flag():
            clear_sent_flag()
            print("🔄 余额已恢复，重置预警状态")
        return

    print(f"\n⚠️ {len(low_accounts)} 个账户低于阈值 ¥{minimum_balance}")

    if has_sent_flag():
        print("🔕 已发送过预警邮件，跳过（避免刷屏）")
        return

    if not to_email:
        print("⚠️ config.qp-cfg 里没有 notification_email，无法发送")
        return

    smtp_path = os.path.join("data", "email.qp-smtp")
    smtp_cfg = parse_qp_config(smtp_path)
    if not smtp_cfg:
        print("⚠️ 未找到 email.qp-smtp，无法发送")
        return

    if send_low_balance_email(smtp_cfg, to_email, low_accounts, minimum_balance):
        set_sent_flag()


def main():
    interval = get_interval()
    if interval is None:
        print("❌ 无法从 config.qp-cfg 读取有效的 testing_time，退出")
        exit(1)

    print(f"⏱️ 定时检测已启动，间隔 {interval} 秒")

    while True:
        print(f"\n {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            check_once()
        except Exception as e:
            print(f"❌ 检测过程出现异常: {e}")

        new_interval = get_interval()
        if new_interval is not None:
            interval = new_interval

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n👋 已停止定时检测")
            break


if __name__ == "__main__":
    main()