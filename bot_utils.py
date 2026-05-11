"""
🔧 bot_utils.py — Shared library for analyst_stock.py and analyst_crypto.py
Provides: markdown-to-HTML renderer with table support, email sender helper.
"""

import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ── HTML TABLE STYLING ────────────────────────────────────────────────────────

def markdown_table_to_html(text: str, accent: str) -> str:
    """
    Detect and convert markdown pipe tables (| col | col |) AND
    tab-separated tables to styled HTML tables.
    """
    bdr  = "#e0c9a0" if accent == "#b35a00" else "#c8ddd0"
    bdr2 = "#e8d8b0" if accent == "#b35a00" else "#d4e8da"
    bg   = "#fffdf0" if accent == "#b35a00" else "#faf8f4"

    def make_table(header_cols, data_rows_cols):
        ths = "".join(
            f'<th style="background:{accent};color:#fff;padding:6px 10px;'
            f'text-align:left;border:1px solid {bdr};">{c}</th>'
            for c in header_cols
        )
        rows_html = "".join(
            "<tr>" + "".join(
                f'<td style="padding:5px 10px;border:1px solid {bdr2};'
                f'vertical-align:top;background:{bg};">{apply_inline(c, accent)}</td>'
                for c in row_cols
            ) + "</tr>"
            for row_cols in data_rows_cols
        )
        return (
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin:12px 0;">'
            f'<thead><tr>{ths}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
        )

    lines  = text.split("\n")
    output = []
    i      = 0

    while i < len(lines):
        line = lines[i]

        # ── Pipe markdown table ──
        if re.match(r'^\s*\|.+\|', line) and not re.match(r'^\s*\|[-:| ]+\|\s*$', line):
            table_lines = []
            while i < len(lines) and re.match(r'^\s*\|', lines[i]):
                table_lines.append(lines[i])
                i += 1
            header_row = table_lines[0]
            data_rows  = [r for r in table_lines[1:] if not re.match(r'^\s*\|[-:| ]+\|\s*$', r)]
            header_cols    = [c.strip() for c in header_row.strip().strip('|').split('|')]
            data_rows_cols = [[c.strip() for c in r.strip().strip('|').split('|')] for r in data_rows]
            output.append(make_table(header_cols, data_rows_cols))

        # ── Tab-separated table (Claude sometimes outputs these) ──
        elif '\t' in line and len(line.split('\t')) >= 3:
            table_lines = []
            while i < len(lines) and ('\t' in lines[i] and len(lines[i].split('\t')) >= 2):
                table_lines.append(lines[i])
                i += 1
            if len(table_lines) >= 2:
                header_cols    = [c.strip() for c in table_lines[0].split('\t')]
                data_rows_cols = [[c.strip() for c in r.split('\t')] for r in table_lines[1:]]
                output.append(make_table(header_cols, data_rows_cols))
            else:
                output.append(line)

        else:
            output.append(line)
            i += 1

    return "\n".join(output)


def style_html_table(html: str, accent: str) -> str:
    """Apply email-friendly inline styles to a raw <table> block (no zebra rows)."""
    bdr  = "#e0c9a0" if accent == "#b35a00" else "#c8ddd0"
    bdr2 = "#e8d8b0" if accent == "#b35a00" else "#d4e8da"

    html = html.replace("<table>",
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin:12px 0;">')
    html = html.replace("<th>",
        f'<th style="background:{accent};color:#fff;padding:6px 10px;text-align:left;border:1px solid {bdr};">')
    html = html.replace("<th ",
        f'<th style="background:{accent};color:#fff;padding:6px 10px;text-align:left;border:1px solid {bdr};" ')
    html = html.replace("<td>",
        f'<td style="padding:5px 10px;border:1px solid {bdr2};vertical-align:top;">')
    html = html.replace("<td ",
        f'<td style="padding:5px 10px;border:1px solid {bdr2};vertical-align:top;" ')
    return html


# ── INLINE MARKDOWN ───────────────────────────────────────────────────────────

def apply_inline(line: str, accent: str) -> str:
    """Convert **bold**, *italic*, and `code` to HTML inline styles."""
    # Bold first (** before *)
    line = re.sub(
        r'\*\*(.+?)\*\*',
        lambda m: f'<strong style="color:{accent};">{m.group(1)}</strong>',
        line
    )
    # Italic
    line = re.sub(
        r'\*(.+?)\*',
        lambda m: f'<em style="color:#666;">{m.group(1)}</em>',
        line
    )
    bg = "#f5ead0" if accent == "#b35a00" else "#eaf3ec"
    line = re.sub(
        r'`(.+?)`',
        lambda m: f'<code style="background:{bg};padding:1px 4px;border-radius:3px;">{m.group(1)}</code>',
        line
    )
    return line


# ── MARKDOWN → HTML ───────────────────────────────────────────────────────────

def markdown_to_html(text: str, accent: str = "#b35a00") -> str:
    """
    Convert Claude's markdown output to email-safe HTML.
    Supports: # H1, ## H2, ### H3, - bullets, **bold**, `code`, ---, <table>.
    accent: hex color for headings, bold, table headers.
              '#b35a00' = orange (crypto)
              '#145c30' = green  (stocks)
    """
    sep_color = "#f0e8c8" if accent == "#b35a00" else "#d4e8da"

    # Step 1: convert markdown pipe tables to HTML tables
    text = markdown_table_to_html(text, accent)

    # Step 2: style any raw <table> blocks Claude wrote directly
    text = re.sub(
        r'<table>.*?</table>',
        lambda m: style_html_table(m.group(0), accent),
        text,
        flags=re.DOTALL
    )

    lines   = text.split("\n")
    output  = []
    in_list = False

    # Pre-process: remove ALL blank lines immediately before any heading
    cleaned = []
    for i, line in enumerate(lines):
        if line.strip() == "":
            # Look ahead — skip this blank if next non-blank line is a heading
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and re.match(r'^#{1,3} ', lines[j]):
                continue  # skip this blank line
        cleaned.append(line)
    lines = cleaned

    # Pre-process: remove --- that immediately follow a heading (redundant divider)
    cleaned2 = []
    for i, line in enumerate(lines):
        if re.match(r'^---+$', line.strip()):
            j = i - 1
            while j >= 0 and cleaned2 and cleaned2[-1].strip() == "":
                j -= 1
            if cleaned2 and re.match(r'^#{1,3} ', cleaned2[-1]):
                continue
        cleaned2.append(line)
    lines = cleaned2

    # Pre-process: remove blank lines between list items (- or *)
    cleaned3 = []
    for i, line in enumerate(lines):
        if line.strip() == "":
            prev = next((cleaned3[j] for j in range(len(cleaned3)-1, -1, -1) if cleaned3[j].strip()), "")
            nxt  = next((lines[j] for j in range(i+1, len(lines)) if lines[j].strip()), "")
            if re.match(r'^[-*] ', prev) and re.match(r'^[-*] ', nxt):
                continue
        cleaned3.append(line)
    lines = cleaned3

    for i, line in enumerate(lines):
        # Pass raw HTML table tags through untouched
        if re.match(r'^\s*<(table|/table|thead|/thead|tbody|/tbody|tr|/tr|td|/td|th|/th)', line):
            if in_list: output.append("</ul>"); in_list = False
            output.append(line)

        elif re.match(r'^#{1,3}\s+.+', line) or re.match(r'^#{1,3}[^ ]', line):
            if in_list: output.append("</ul>"); in_list = False
            level = len(re.match(r'^(#{1,3})', line).group(1))
            title = re.sub(r'^#{1,3}\s*', '', line)
            if level == 1:
                output.append(
                    f'<div style="color:{accent};font-size:20px;font-weight:bold;margin:12px 0 6px;'
                    f'border-bottom:2px solid {sep_color};padding-bottom:6px;">{title}</div>'
                )
            elif level == 2:
                output.append(f'<div style="color:{accent};font-size:16px;font-weight:bold;margin:10px 0 4px;">{title}</div>')
            else:
                output.append(f'<div style="color:#2c2c2c;font-size:14px;font-weight:bold;margin:8px 0 4px;">{title}</div>')

        elif re.match(r'^[-*] (.+)', line):
            if not in_list:
                output.append('<ul style="margin:4px 0 4px 16px;padding:0;">')
                in_list = True
            inner = apply_inline(re.sub(r'^[-*] ', '', line), accent)
            output.append(f'<li style="margin:3px 0;">{inner}</li>')

        elif re.match(r'^---+$', line):
            if in_list: output.append("</ul>"); in_list = False
            output.append(f'<hr style="border:none;border-top:1px solid {sep_color};margin:16px 0;">')

        elif line.strip() == "":
            if in_list: output.append("</ul>"); in_list = False
            output.append("<br>")

        else:
            if in_list: output.append("</ul>"); in_list = False
            output.append(f'<p style="margin:4px 0;line-height:1.7;">{apply_inline(line, accent)}</p>')

    if in_list:
        output.append("</ul>")
    return "\n".join(output)


# ── EMAIL SENDER ──────────────────────────────────────────────────────────────

def send_email(
    analysis:        str,
    run_date:        str,
    sender_email:    str,
    sender_password: str,
    recipients:      list,
    subject:         str,
    header_title:    str,
    header_color:    str,
    bg_color:        str,
    accent:          str,
    footer:          str = "Financial Trend Bot · Not financial advice",
):
    """Universal email sender with themed HTML template."""
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender_email
    msg["To"]      = ", ".join(recipients)

    text_part = MIMEText(analysis, "plain")
    html_body = markdown_to_html(analysis, accent=accent)

    sep = "#f0e8c8" if accent == "#b35a00" else "#ddd"
    html = f"""<html><body style="font-family:'Georgia',serif;max-width:680px;margin:auto;
                   background:{bg_color};color:#2c2c2c;padding:32px;">
  <div style="border-left:4px solid {header_color};padding-left:20px;margin-bottom:24px;">
    <h1 style="color:{header_color};font-size:22px;margin:0;">{header_title}</h1>
    <p style="color:#999;margin:4px 0 0;">{run_date}</p>
  </div>
  <div style="line-height:1.8;font-size:15px;">{html_body}</div>
  <hr style="border-color:{sep};margin-top:40px;">
  <p style="color:#bbb;font-size:12px;">{footer}</p>
</body></html>"""

    html_part = MIMEText(html, "html")
    msg.attach(text_part)
    msg.attach(html_part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipients, msg.as_string())

    print("✅ Email sent!")