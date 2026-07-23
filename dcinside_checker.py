# -*- coding: utf-8 -*-
"""
디시인사이드 갤러리 '키워드 검색' 크롤러 + 디스코드 알림 봇 (매시간 실행)

- 게시판 전체 글이 아니라, 지정한 키워드로 검색한 결과만 확인
  (DC인사이드 자체 검색 기능: s_type=search_subject_memo → 제목+내용 검색)
- 키워드마다 최근 게시글을 최대 RESULTS_PER_KEYWORD 건까지만 확인
- 같은 글이 여러 키워드에 동시에 매칭되면, 매칭된 모든 키워드 배열에 각각 기록됨
  (단, 같은 실행 안에서 디스코드 중복 알림은 가지 않도록 방지)
- 새 글이 있으면 지정된 디스코드 채널로 알림 전송 (메시지 맨 앞에 [키워드:...] 표시)
- 이미 본 글 목록은 seen_dcinside.json 에 "갤러리 > 키워드 > 글ID 리스트" 형태로 저장
  (GitHub Actions가 자동 커밋)
"""

import json
import os
import re
import sys
import time
from urllib.parse import urljoin, quote, urlparse, parse_qs

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
        "gallery_id": "airforce",  # 실제 게시글 링크의 id= 파라미터와 대조하기 위함
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

# 키워드당 최근 몇 건까지만 확인할지
RESULTS_PER_KEYWORD = 20

# 위 개수를 채우기 위해 최대 몇 페이지까지 넘겨볼지 (페이지당 대략 20개 안팎)
MAX_PAGES_PER_KEYWORD = 3

# 키워드별로 seen_dcinside.json 에 최대 몇 개의 글 ID까지 저장해둘지
# (오래된 것부터 자동으로 정리됨)
MAX_STORED_IDS_PER_KEYWORD = 200

# 디시인사이드는 봇으로 보이는 요청을 차단하는 경우가 있어서
# 실제 브라우저와 비슷한 헤더를 사용함
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://gall.dcinside.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 환경변수 NOTIFY_ON_FIRST_RUN=1 로 설정하면, 기준선이 없는 첫 실행이더라도
# 지금 찾은 글들을 전부 '새 글'처럼 취급해서 실제로 디스코드 알림을 보냄
# (평소엔 설정하지 않는 게 정상 - 매번 20건씩 재알림되는 걸 막기 위함)
FORCE_NOTIFY_ON_FIRST_RUN = os.environ.get("NOTIFY_ON_FIRST_RUN") == "1"


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
    gallery_id = board["gallery_id"]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_marker not in href:
            continue

        # 디시인사이드 페이지 사이드바에는 '실시간 베스트(디시베스트, id=dcbest)' 등
        # 전혀 다른 갤러리의 인기글 링크가 항상 같이 붙어 있음.
        # /board/view/ 라는 문자열만으로는 이런 무관한 링크까지 걸리므로,
        # 반드시 이 갤러리(gallery_id)의 글인지 id= 파라미터로 한 번 더 확인한다.
        query = parse_qs(urlparse(href).query)
        href_gallery_id = query.get("id", [None])[0]
        if href_gallery_id != gallery_id:
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


def fetch_recent_notices_for_keyword(board, keyword, limit=RESULTS_PER_KEYWORD,
                                      max_pages=MAX_PAGES_PER_KEYWORD):
    """특정 키워드의 검색 결과를 최신순으로 최대 limit건까지만 모아서 반환"""
    collected = []
    for page in range(1, max_pages + 1):
        try:
            page_notices = fetch_page(board, keyword, page)
        except Exception as e:
            print(f"[오류] '{board['name']}' 키워드 '{keyword}' {page}페이지 가져오기 실패: {e}")
            break

        if not page_notices:
            break  # 더 이상 검색 결과가 없는 페이지에 도달

        collected.extend(page_notices)

        if len(collected) >= limit:
            break

        time.sleep(1)  # 연속 요청 시 차단 방지용 딜레이

    return collected[:limit]


def send_discord_message(webhook_url, board_name, notice):
    if not webhook_url:
        print(f"[경고] '{board_name}' 담당 웹훅 환경변수가 설정되어 있지 않습니다.")
        return False

    # 첫 줄 맨 앞에 어떤 키워드로 잡힌 글인지 표시
    lines = [f"[키워드:{notice['keyword']}] **[{board_name}]** {notice['title']}"]
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

        # ---- seen 데이터 준비: { "키워드": ["id1", "id2", ...], ... } ----
        board_seen = seen.setdefault(name, {})
        for kw in KEYWORDS:
            board_seen.setdefault(kw, [])

        # 어떤 키워드로 이미 저장되어 있든, 한 번이라도 본 글이면 "이미 본 글"로 취급
        all_seen_ids = set()
        for kw in KEYWORDS:
            all_seen_ids.update(board_seen[kw])

        is_first_run = len(all_seen_ids) == 0

        # ---- 키워드별로 최근 결과 수집 ----
        all_notices = []
        for keyword in KEYWORDS:
            try:
                keyword_notices = fetch_recent_notices_for_keyword(board, keyword)
            except Exception as e:
                print(f"[오류] '{name}' 키워드 '{keyword}' 검색 실패: {e}")
                continue

            print(f"[검색] '{name}' 키워드 '{keyword}' → {len(keyword_notices)}건 (최근 {RESULTS_PER_KEYWORD}건 이내)")
            all_notices.extend(keyword_notices)
            time.sleep(1)  # 키워드 간 요청 딜레이 (차단 방지)

        if not all_notices:
            print(f"[알림] '{name}' 에서 검색 결과를 하나도 못 가져왔습니다. "
                  f"사이트 구조가 바뀌었거나 접근이 차단됐을 수 있습니다.")

        # 같은 글이 여러 키워드에 동시에 매칭될 수 있으므로 id 기준 중복 제거
        # (먼저 매칭된 키워드 하나에만 귀속시켜, 같은 글이 여러 키워드 배열에
        #  중복으로 쌓이지 않도록 함)
        unique_notices = {}
        for n in all_notices:
            if n["id"] not in unique_notices:
                unique_notices[n["id"]] = n
        notices = list(unique_notices.values())

        if is_first_run and not FORCE_NOTIFY_ON_FIRST_RUN:
            # 첫 실행: 발견된 글들을 '이미 본 것'으로 기준선만 잡고, 알림은 보내지 않음
            baseline_count = 0
            for n in notices:
                if n["id"] not in all_seen_ids:
                    board_seen[n["keyword"]].append(n["id"])
                    baseline_count += 1
            print(f"[초기화] '{name}' 키워드 매칭 {baseline_count}건을 기준선으로 저장 (알림 없음)")
        else:
            new_notices = [n for n in notices if n["id"] not in all_seen_ids]
            # id 오름차순(작성순 근사치)으로 정렬해서 오래된 글부터 알림
            new_notices.sort(key=lambda n: int(n["id"]))

            for notice in new_notices:
                print(f"[새 글] ({name}) [{notice['keyword']}] {notice['title']}")
                success = send_discord_message(webhook_url, name, notice)
                if success:
                    board_seen[notice["keyword"]].append(notice["id"])
                    total_new += 1
                else:
                    print(f"[보류] ({name}) {notice['title']} → 다음 실행 때 재시도됨")
                time.sleep(1)

        # 키워드별 저장 개수가 너무 많아지지 않도록 최근 것만 유지
        for kw in KEYWORDS:
            board_seen[kw] = board_seen[kw][-MAX_STORED_IDS_PER_KEYWORD:]

    save_seen(seen)
    print(f"완료: 새 글 {total_new}건 처리")


if __name__ == "__main__":
    sys.exit(main())