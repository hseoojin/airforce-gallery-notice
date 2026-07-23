# -*- coding: utf-8 -*-
"""
디스코드 웹훅 연결 테스트용 스크립트
사용법:
    PowerShell에서 먼저 환경변수 설정:
        $env:DISCORD_WEBHOOK_URL_AIRFORCE="웹훅URL"
    그 다음 실행:
        python test_webhook.py
"""

import os
import sys

import requests

WEBHOOK_ENV = "DISCORD_WEBHOOK_URL_AIRFORCE"


def main():
    webhook_url = os.environ.get(WEBHOOK_ENV)

    if not webhook_url:
        print(f"[오류] 환경변수 '{WEBHOOK_ENV}' 가 설정되어 있지 않습니다.")
        print("PowerShell에서 먼저 아래처럼 설정한 뒤 다시 실행하세요:")
        print(f'  $env:{WEBHOOK_ENV}="웹훅URL"')
        sys.exit(1)

    payload = {"content": "✅ 웹훅 연결 테스트입니다 (dcinside_checker 프로젝트)"}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[실패] 요청 자체가 안 나갔습니다: {e}")
        sys.exit(1)

    if resp.status_code in (200, 204):
        print("[성공] 디스코드 채널에 테스트 메시지가 전송되었습니다. 채널을 확인해보세요.")
    else:
        print(f"[실패] 상태 코드 {resp.status_code}")
        print(f"응답 내용: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()