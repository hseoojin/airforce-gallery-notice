# -*- coding: utf-8 -*-
"""
디시인사이드 갤러리 크롤러 + 디스코드 알림 봇 (매시간 실행)

- 새 글이 있으면 지정된 디스코드 채널로 알림 전송
- 이미 본 글 목록은 seen_dcinside.json 에 저장 (GitHub Actions가 자동 커밋)
"""

import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# 설정: 확인할 갤러리 목록
# id_param    : 게시글 고유 번호가 들어있는 URL 파라미터 이름
# link_marker : 게시글 링크임을 구분하는 URL 안의 특징적인 문자열
# ----------------------------------------------------------------------
BOARDS = [
    {
        "name": "공군 갤러리",
        "url": "https://gall.dcinside.com/board/lists/?id=airforce",
        "base": "https://gall.dcinside.com",
        "webhook_env": "DISCORD_WEBHOOK_URL_AIRFORCE",
        "id_param": "no",
        "link_marker": "/board/view/",
    },
]

SEEN_FILE = "seen_dcinside.json"

# 디시인사이드는 봇으로 보이는 요청을 차단하는 경우가 있어서
# 실제 브라우저와 비슷한 헤더를 사용함
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://gall.dcinside.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def fetch_notices(board):
    resp = requests.get(board["url"], headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    notices = []
    seen_id_on_page = set()

    id_param = board["id_param"]
    link_marker = board["link_marker"]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_marker not in href:
            continue

        match = re.search(rf"{id_param}=(\d+)", href)
        if not match:
            continue
        post_id = match.group(1)

        if post_id in seen_id_on_page:
            continue

        title = a.get_text(strip=True)
        if not title:
            continue

        seen_id_on_page.add(post_id)
        author, date = "", ""

        tr = a.find_parent("tr")
        if tr:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            # 디시인사이드는 날짜가 'YYYY-MM-DD' / 'MM.DD' / 'HH:MM' 등 여러 형식으로 표시됨
            date_candidates = [
                t
                for t in tds
                if re.match(r"^\d{4}-\d{2}-\d{2}$", t)
                or re.match(r"^\d{2}\.\d{2}$", t)
                or re.match(r"^\d{2}:\d{2}$", t)
            ]
            if date_candidates:
                date = date_candidates[0]
                idx = tds.index(date)
                if idx - 1 >= 0:
                    author = tds[idx - 1]

        full_link = urljoin(board["base"], href)

        notices.append(
            {
                "id": post_id,
                "title": title,
                "author": author,
                "date": date,
                "link": full_link,
            }
        )

    return notices


def send_discord_message(webhook_url, board_name, notice):
    if not webhook_url:
        print(f"[경고] '{board_name}' 담당 웹훅 환경변수가 설정되어 있지 않습니다.")
        return False

    lines = [f"**[{board_name}]** {notice['title']}"]
    meta = []
    if notice.get("author"):
        meta.append(notice["author"])
    if notice.get("date"):
        meta.append(notice["date"])
    if meta:
        lines.append(" · ".join(meta))
    lines.append(notice["link"])

    payload = {"content": "\n".join(lines)}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[경고] 디스코드 전송 중 예외 발생: {e}")
        return False

    if resp.status_code == 429:
        retry_after = 1.0
        try:
            retry_after = float(resp.json().get("retry_after", 1.0))
        except Exception:
            pass
        print(f"[경고] 디스코드 rate limit, {retry_after:.1f}초 대기 후 재시도")
        time.sleep(retry_after + 0.5)
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            print(f"[경고] 재시도 중 예외 발생: {e}")
            return False

    if resp.status_code >= 300:
        print(f"[경고] 디스코드 전송 실패 ({resp.status_code}): {resp.text}")
        return False

    return True


def main():
    seen = load_seen()
    total_new = 0

    for board in BOARDS:
        name = board["name"]
        webhook_url = os.environ.get(board["webhook_env"])
        seen.setdefault(name, [])
        already_seen = set(seen[name])

        try:
            notices = fetch_notices(board)
        except Exception as e:
            print(f"[오류] '{name}' 크롤링 실패: {e}")
            continue

        if not notices:
            print(f"[알림] '{name}' 에서 글을 하나도 못 가져왔습니다. "
                  f"사이트 구조가 바뀌었거나 접근이 차단됐을 수 있습니다.")

        new_notices = [n for n in notices if n["id"] not in already_seen]
        new_notices.reverse()

        for notice in new_notices:
            print(f"[새 글] ({name}) {notice['title']}")
            success = send_discord_message(webhook_url, name, notice)
            if success:
                seen[name].append(notice["id"])
                total_new += 1
            else:
                print(f"[보류] ({name}) {notice['title']} → 다음 실행 때 재시도됨")
            time.sleep(1)

        # 목록이 너무 커지지 않도록 최근 500개만 유지 (갤러리는 글이 많음)
        seen[name] = seen[name][-500:]

    save_seen(seen)
    print(f"완료: 새 글 {total_new}건 처리")


if __name__ == "__main__":
    sys.exit(main())