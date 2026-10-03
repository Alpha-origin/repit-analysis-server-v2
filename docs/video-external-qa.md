# 실제 API 서버·S3 연동 QA

R1 인계 명세의 시나리오 6개를 `tests/video/qa_external.py`로 실행한다.
배포 검증은 구현 이후로 미뤘으므로 현재 실제 환경 실행 결과는 없다.
로컬 `qa_scenario.py`는 S3·수신측을 대체하며 이 실환경 검증을 대신하지 않는다.

## 준비와 실행

1. [manifest 예제](video-external-manifest.example.json)를 보호된 로컬 경로로 복사한다.
   base URL 두 개, 모든 callbackUrl, 실제 사용자/면접 ID, S3 URL·실제 fileSize를 채운다.
2. Chrome 녹화 영상을 실제 S3에 업로드하고 불변 키 또는 versionId를 사용한다.
   유효 URL·동일 객체의 재서명 URL·실제로 만료된 URL을 각각 준비한다.
   동일 요청 재서명은 최초 접수 URL을 교체하지 않는다. 만료 복구에는 새 requestId를 사용한다.
3. 스테이징 수신측의 테스트용 장애 제어·관찰 경로에 맞춰 manifest의 receiver step을 조정한다.
   기존 텍스트 5종·음성 요청 본문도 실제 테스트 입력으로 바꾼다. API 서버 코드는 이 저장소에서 수정하지 않는다.
4. 배포 환경에서 VIDEO_ENABLED와 AUDIO_ENABLED를 켜고 영상·음성 워커를 준비한다.
   텍스트 생성은 정상 작동하는 Anthropic 설정·테스트 PDF/저장소 등도 필요하다.
5. 환경변수 VIDEO_API_TOKEN, APP_INTERNAL_CALLBACK_TOKEN, QA_RECEIVER_TOKEN을 제공한다.
   QA_RECEIVER_TOKEN은 수신측의 테스트 제어/관찰 인증용 별도 토큰이다. 토큰을 인자로 전달하지 않는다.

```bash
PYTHONPATH=src .venv/bin/python -m tests.video.qa_external \
  --manifest /private/path/video-external.json \
  --evidence-dir .omo/evidence/video-analysis-api/external
```

manifest 누락·구성 오류는 exit 2/BLOCKED, 시나리오 실패는 exit 1/FAIL이다.
여섯 시나리오 모두 성공해야 exit 0/PASS다. HTTPS만 사용하고 리다이렉트·프록시 상속을 허용하지 않는다.
증거에는 요청 본문이나 헤더를 기록하지 않으며, 수신 응답에서도 서명 쿼리·토큰을 제거한다.
합성 테스트 영상·테스트 식별자만 쓰고 manifest나 개인 영상을 커밋하지 않는다.

## 시나리오

| 이름 | 인계 기준 | 확인 |
|---|---|---|
| valid | 실제 브라우저 영상 접수·종료 | callback body = GET.result, tokenOk=true |
| replay | 202 유실 후 재전송 | 같은 본문/재서명은 기존 jobId |
| expired-source | 서명 만료 URL | SOURCE_ACCESS_DENIED_OR_EXPIRED. 새 requestId로 정상 URL 재접수 |
| size-mismatch | fileSize를 1바이트 크게 | SOURCE_SIZE_MISMATCH |
| callback-failure | 503 한 번, 401 영구 거절 | 503 뒤 5초 이상 후 성공, 401은 한 번 후 전달 failed/GET 복구 |
| legacy-callbacks | 텍스트 5종·음성 | 토큰 헤더, 본문·기존 재시도 계약 유지 |

## 수신측 관찰 데이터

예제의 `/qa/*`는 **수신측이 이미 제공하거나 별도로 준비할 테스트 기능의 인터페이스**다.
이 저장소는 실제 API 서버에 해당 엔드포인트가 있다고 가정해 실행하지 않는다.
수신측 경로·응답이 다르면 manifest를 맞춘다.

- POST `/qa/receiver/mode`: `normal`, `503-once`, `401` 모드를 설정하고 `mode`를 되돌려준다.
  `callback-failure.cleanup`은 성공/실패와 관계없이 정상 모드를 복원한다.
- GET `/qa/callbacks/{jobId}`: 저장된 `body`, 검증된 `tokenOk`, 전송 시도 `attempts`,
  `statusCodes`, 503 실패와 성공 사이의 실제 간격 `retryDelaySeconds`를 반환한다.
  503-once에서는 `[503,204]`·2회·간격 5초 이상, 401에서는 `[401]`·1회인지 확인한다.
  수신측 성공 코드가 200이면 예제의 204를 실제 코드로 바꾼다.
- GET `/qa/legacy/{jobId}`: `tokenOk`, `bodyContractOk`, `retryContractOk`를 반환한다.
  뒤의 두 값은 수신측의 실제 기록과 기존 계약 대조 결과여야 한다. 상수 true로 대체하지 않는다.
  기존 재시도 검증에는 수신측 장애 주입과 시간/횟수 관찰을 추가한다.

## manifest step 규칙

`target`은 analysis/receiver, `method`는 GET/POST다. 각 step은 HTTP 상태와 응답의 `expected` 필드를 검사한다.
`expected` 키는 `result.result.analysis.error.code`처럼 JSON dot path이며, 숫자는 배열 인덱스다.
`minimum`은 지정한 숫자 필드의 하한을 검사한다(예: retryDelaySeconds ≥ 5).
빈 키는 JSON 본문 전체를 뜻한다. `save_as`로 저장한 본문을 `$snapshot.result`로 비교할 수 있다.
path의 `{accepted.jobId}`는 앞 단계에서 저장한 값을 사용한다.
GET만 `poll_seconds` 동안 반복할 수 있고 POST는 자동 재전송하지 않는다.

매 실행마다 `$run.valid`, `$run.session`, `$run.expired`, `$run.recovery`, `$run.mismatch`,
`$run.callback503`, `$run.callback401`, `$run.audio`에 새 식별자를 만든다.
재전송 시나리오는 `$run.valid`를 재사용하고 다른 시나리오는 새 ID를 사용하므로 이전 실행의 결과를 성공으로 오인하지 않는다.

최종 수용에는 증거 JSON의 response와 실제 분석 DB 작업·콜백 상태, 수신측 저장 상태를 대조한다.
추가로 분석 서버 로그에서 서명 URL·토큰 노출이 없는지 확인한다.
