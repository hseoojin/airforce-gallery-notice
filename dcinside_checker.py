# -*- coding: utf-8 -*-
"""
디시인사이드 갤러리 '키워드 검색' 크롤러 + 디스코드 알림 봇 (매시간 실행)

- 게시판 전체 글이 아니라, 지정한 키워드로 검색한 결과만 확인
  (DC인사이드 자체 검색 기능: s_type=search_subject_memo → 제목+내용 검색)
- 새 글이 있으면 지정된 디스코드 채널로 알림 전송
- 이미 본 글 목록은 seen_dcinside.json 에 저장 (GitHub Actions가 자동 커밋)
"""

import json
import os
import re
import sys
import time
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# 설정: 확인할 갤러리 목록
# url         : 페이지/검색 파라미터가 붙기 전의 게시판 기본 주소
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

# ----------------------------------------------------------------------
# 검색할 키워드 목록 (제목+내용 대상 검색)
# ----------------------------------------------------------------------
KEYWORDS = [
    "통전",
    "통신전자전기",
    "작통",
    "기통",
    "작전통신",
    "기반통신",
    "프로그래밍 기능사",
    "육무통",
    "항무통",
    "운전",
]

# 검색 결과를 몇 페이지까지 확인할지
# 첫 실행(기준선 없음): 넉넉하게 여러 페이지 확인
INITIAL_SEARCH_PAGES = 3
# 평상시(매시간): 검색 결과라 전체 글보다 양이 적으므로 이 정도면 충분
NORMAL_SEARCH_PAGES = 2

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


def build_search_url(board, keyword, page):
    """키워드 검색 결과 페이지 URL 생성

    예: https://gall.dcinside.com/board/lists/?id=airforce&page=2
        &search_pos=&s_type=search_subject_memo&s_keyword=%EC%9A%B4%EC%A0%84
    """
    return (
        f"{board['url']}&page={page}"
        f"&search_pos=&s_type=search_subject_memo"
        f"&s_keyword={quote(keyword)}"
    )


def fetch_page(board, keyword, page):
    """특정 키워드로 검색한 결과의 특정 페이지를 가져와서
    (id, 제목, 작성자, 날짜, 링크, 키워드) 리스트로 반환"""
    url = build_search_url(board, keyword, page)
    resp = requests.get(url, headers=HEADERS, timeout=15)
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
                "keyword": keyword,
            }
        )

    return notices


def fetch_notices_for_keyword(board, keyword, pages=1):
    """1페이지부터 지정한 페이지 수까지 순회하며
    특정 키워드의 검색 결과를 모두 모아서 반환"""
    all_notices = []
    for page in range(1, pages + 1):
        try:
            page_notices = fetch_page(board, keyword, page)
        except Exception as e:
            print(f"[오류] '{board['name']}' 키워드 '{keyword}' {page}페이지 가져오기 실패: {e}")
            break

        if not page_notices:
            break  # 더 이상 검색 결과가 없는 페이지에 도달

        all_notices.extend(page_notices)
        time.sleep(1)  # 연속 요청 시 차단 방지용 딜레이

    return all_notices


def send_discord_message(webhook_url, board_name, notice):
    if not webhook_url:
        print(f"[경고] '{board_name}' 담당 웹훅 환경변수가 설정되어 있지 않습니다.")
        return False

    lines = [f"**[{board_name}]** ({notice['keyword']}) {notice['title']}"]
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

        is_first_run = len(already_seen) == 0
        pages_to_fetch = INITIAL_SEARCH_PAGES if is_first_run else NORMAL_SEARCH_PAGES

        # 키워드별로 검색 결과를 모아서 하나의 리스트로 합침
        all_notices = []
        for keyword in KEYWORDS:
            try:
                keyword_notices = fetch_notices_for_keyword(board, keyword, pages=pages_to_fetch)
            except Exception as e:
                print(f"[오류] '{name}' 키워드 '{keyword}' 검색 실패: {e}")
                continue

            print(f"[검색] '{name}' 키워드 '{keyword}' → {len(keyword_notices)}건")
            all_notices.extend(keyword_notices)
            time.sleep(1)  # 키워드 간 요청 딜레이 (차단 방지)

        if not all_notices:
            print(f"[알림] '{name}' 에서 검색 결과를 하나도 못 가져왔습니다. "
                  f"사이트 구조가 바뀌었거나 접근이 차단됐을 수 있습니다.")

        # 같은 글이 여러 키워드에 동시에 매칭될 수 있으므로 id 기준 중복 제거
        # (먼저 매칭된 키워드 표시를 그대로 유지)
        unique_notices = {}
        for n in all_notices:
            if n["id"] not in unique_notices:
                unique_notices[n["id"]] = n
        notices = list(unique_notices.values())

        if is_first_run:
            # 첫 실행: 검색된 글들을 '이미 본 것'으로 기준선만 잡고, 알림은 보내지 않음
            new_ids = [n["id"] for n in notices if n["id"] not in already_seen]
            seen[name].extend(new_ids)
            print(f"[초기화] '{name}' 키워드 매칭 {len(new_ids)}건을 기준선으로 저장 (알림 없음)")
        else:
            new_notices = [n for n in notices if n["id"] not in already_seen]
            # id 오름차순(작성순 근사치)으로 정렬해서 오래된 글부터 알림
            new_notices.sort(key=lambda n: int(n["id"]))

            for notice in new_notices:
                print(f"[새 글] ({name}) [{notice['keyword']}] {notice['title']}")
                success = send_discord_message(webhook_url, name, notice)
                if success:
                    seen[name].append(notice["id"])
                    total_new += 1
                else:
                    print(f"[보류] ({name}) {notice['title']} → 다음 실행 때 재시도됨")
                time.sleep(1)

        # 목록이 너무 커지지 않도록 최근 1000개만 유지
        seen[name] = seen[name][-1000:]

    save_seen(seen)
    print(f"완료: 새 글 {total_new}건 처리")


if __name__ == "__main__":
    sys.exit(main())