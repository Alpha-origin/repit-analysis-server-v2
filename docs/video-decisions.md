# 영상 판정·콜백 결정 기록

2026-10-03. 제공된 HTML 인계 명세의 R5 세 항목을 현재 코드·테스트와 대조했다.
아래 현재 동작을 유지하며, 모델·실제 수신측 결정과 배포 검증은 별도로 진행한다.

## 프레임률

원래 인접 간격 규칙은 Chrome MediaRecorder의 낮은 평균 프레임률 영상에서도
간헐적인 2~4ms 간격을 과속으로 판정했다. 현재 규칙은 PTS가 엄격히 증가하고,
마지막 프레임에서 거슬러 올라가 1초 미만인 슬라이딩 구간의 프레임 수가 max_fps 이하여야 한다.
끝점이 정확히 1초인 프레임은 같은 구간에 포함하지 않는다. 전체 프레임률 검사에는 time_base 한 tick을 반영한다.
지속 61fps·120fps와 1초 안의 61프레임 버스트는 거절하며, 59.94/60fps·정상 VFR·Chrome 지터는 허용한다.

검증: `test_validation.py`, `test_real_media.py`, `test_browser_media.py`.
다시 인접 간격 규칙으로 바꾸려면 실제 브라우저 녹화 호환성을 먼저 검증해야 한다.

## WebM 길이

ffprobe의 비트레이트 추정값은 제한 판정에 사용하지 않는다.
헤더 EBML Duration이 있는 경우에만 선언 길이로 쓰고, 없는 경우 전체 디코딩의 PTS·마지막 프레임 길이/
마지막 양의 간격으로 실제 길이를 계산한다. 선언값과 디코딩값 중 큰 값으로 길이 제한을 판정한다.
따라서 “헤더의 길이 값만 사용”은 **선언 길이의 출처**에 관한 설명이며 실제 전체 길이는 디코딩으로 확인한다.
유효한 선언 길이도 마지막 프레임 길이도 없는 한 프레임 영상은 VIDEO_DECODE_FAILED로 종료한다.

검증: `test_probe.py`, `test_validation.py`, 실제 Chrome Duration 없는 샘플.
60분 경계, 짧은 선언값으로 제한을 우회하려는 파일, 과도한 선언값을 기존 검사로 확인한다.

## 기존 콜백

production에서는 현재 허용 목록 밖 주소·긴급 차단 주소로 실제 HTTP 요청을 보내지 않는다.
허용된 HTTPS 수신측에 APP_INTERNAL_CALLBACK_TOKEN을 넣고 기존 본문·재시도를 유지한다.
development에서는 로컬 HTTP 수신측 호환을 유지하며 토큰을 첨부하지 않는다.

검증: `tests/security/test_legacy_callbacks.py`, `test_callback_transport.py`, `test_redaction.py`.
실제 텍스트 5종·음성 수신측의 토큰/본문/재시도 대조는 외부 QA의 legacy-callbacks 시나리오로 수행한다.
수신측 토큰 수용 → 모든 발신 프로세스 교체 → 수신측 검증 강제 순서를 유지한다.

영상 조회/접수의 VIDEO_API_TOKEN과 발신 콜백 토큰은 별개다. 토큰이나 서명 URL을 로그·증거·설정 예제에 남기지 않는다.
