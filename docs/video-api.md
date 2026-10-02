# API 서버 ↔ AI 서버 영상 분석 통신

면접 영상 1개를 접수해 실제 파일을 검사하고, 고정된 종료 결과를 콜백과 조회로 돌려준다.
음성(`/analysis/audio`)·텍스트 기능과는 저장소·워커·재시도·보관 정책이 모두 분리돼 있다.

> **현재 범위:** 행동 분석 모델은 아직 연결되지 않았다. 파일이 유효해도 결과는
> `status: "unavailable"` + `analysis.error.code: "ANALYZER_NOT_CONFIGURED"` 이다.
> 성공(`ready`)을 지어내지 않는다. 행동 데이터(`analysis.data`) 스키마는 모델이 정해질 때 별도 버전으로 정의한다.

운영(설치·워커·보관)은 [video-analysis.md](video-analysis.md) 참고.

## 인증

두 엔드포인트 모두 `X-Internal-Token: <VIDEO_API_TOKEN>` 헤더가 필요하다. 본문 해석이나 작업 조회보다 먼저 검사하며,
틀리면 작업 존재 여부와 무관하게 같은 `401` 을 돌려준다.

AI 서버가 보내는 콜백에는 **다른** 토큰(`APP_INTERNAL_CALLBACK_TOKEN`)이 같은 헤더 이름으로 실린다.
수신측은 이 값을 검증해야 한다. 두 토큰은 서로 바꿔 쓰지 않는다.

## POST /analysis/video

<!-- example: request -->
```json
{
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "callbackUrl": "https://api.example.com/api/analyses/video/callback",
  "video": {
    "videoId": "501",
    "fileUrl": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/interview-videos/42/501.webm?versionId=3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=...",
    "contentType": "video/webm",
    "fileSize": 73400320,
    "uploadedAt": "2026-10-02T09:00:00+09:00"
  }
}
```

- 모든 필드 필수, 정의되지 않은 필드는 어느 깊이에서든 거부(`422`). **camelCase 만** 받는다.
- ID 는 공백뿐이 아닌 문자열(최대 200자). 앞뒤 공백을 임의로 지우지 않는다.
- `fileSize` 는 양의 정수(문자열·실수·불리언 불가). `uploadedAt` 은 타임존이 있는 ISO 8601 **문자열**.
- `contentType` 은 `video/*` 형식만 본다. 실제 형식은 파일을 열어 판단한다(MIME·확장자는 믿지 않음).
- URL 은 HTTPS(기본 443), 사용자정보·fragment·제어문자·공백·역슬래시·IP 주소·중복 쿼리 키를 허용하지 않는다.
- **새 요청에만** 현재 정책을 적용한다: `fileSize` ≤ 1,000,000,000, `fileUrl` 호스트가 허용된 S3 엔드포인트,
  `callbackUrl` 호스트가 허용 목록. 위반 시 `422 REQUEST_NOT_ADMITTED`.

202 응답(요청·정책·작업이 한 트랜잭션으로 저장된 뒤에만 보낸다):

<!-- example: accepted -->
```json
{"jobId": "9f0c1e...", "requestId": "video-request-001", "sessionId": "b1c2d3", "status": "accepted"}
```

### 재전송·중복 규칙

비교 키는 `(sessionId, requestId)`, 비교 대상은 **요청 내용만**이다(서버 정책은 비교하지 않는다).

| 상황 | 응답 |
|---|---|
| 같은 내용 재전송(202 유실 등) | `202` + 기존 `jobId` |
| 같은 내용, `fileUrl` 의 SigV4 서명 파라미터만 바뀜(재서명) | `202` + 기존 `jobId` |
| 같은 키, 다른 내용 | `409 REQUEST_ID_CONFLICT` |
| 결과 보관 만료 후 같은 내용 | `410 JOB_EXPIRED` |
| 결과 보관 만료 후 다른 내용 | `409` |
| 식별 기록까지 만료(종료 후 180일) | 새 작업으로 접수 |

- 재서명으로 간주해 무시하는 쿼리는 `X-Amz-Algorithm, X-Amz-Credential, X-Amz-Date, X-Amz-Expires,
  X-Amz-SignedHeaders, X-Amz-Signature, X-Amz-Security-Token` **정확히 이 7개**뿐이다. `versionId`,
  `partNumber`, `response-*`, 그 밖의 `X-Amz-*` 와 경로(대소문자·퍼센트 인코딩 포함)는 내용으로 비교한다.
- 이미 접수된 작업은 **최초에 받은 URL** 로 다운로드한다. 재서명 URL 로 바꿔 끼우지 않는다.
  URL 이 만료돼 실패했다면 **새 `requestId`** 와 새 URL 로 다시 요청한다.
- 같은 객체 키의 내용이 바뀌면 안 된다. 불변 객체 키 또는 `versionId` 를 사용한다(다운로드 SHA-256 은
  서버 내부 무결성 기록일 뿐, 요청자가 의도한 원본과의 비교값이 아니다).

### 오류 응답 형식

```json
{"code": "INVALID_REQUEST", "message": "요청 형식이 올바르지 않습니다.", "details": [{"loc": ["video", "fileSize"], "type": "int_type"}]}
```

`401 UNAUTHORIZED`, `409 REQUEST_ID_CONFLICT`, `410 JOB_EXPIRED`, `413 PAYLOAD_TOO_LARGE`(본문 64KiB 초과),
`422 INVALID_JSON | INVALID_REQUEST | REQUEST_NOT_ADMITTED`, `503 TEMPORARILY_UNAVAILABLE`(같은 요청으로 재시도).
입력값(서명 URL 등)은 오류 응답에 되돌려주지 않는다.

## 종료 콜백

작업이 끝나면 `callbackUrl` 로 한 번 고정된 본문을 POST 한다. 같은 본문이 조회 응답의 `result` 로도 제공된다.

| 상위 `status` | 의미 | `result` | `error` |
|---|---|---|---|
| `ready` / `partial` | 분석 완료(부분 결과도 **종료**다. 진행 중이 아니다) | 영상 결과 | `null` |
| `unavailable` | 문서는 만들었지만 결과를 낼 수 없음(파일 문제 또는 분석기 미연결) | 영상 결과 | `null` |
| `failed` | 종료 문서 생성 자체가 실패 | `null` | 공개 오류 |

분석기 미연결(현재 운영 기본):

<!-- example: callback-unconfigured -->
```json
{
  "schemaVersion": "1",
  "jobId": "9f0c1e",
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "status": "unavailable",
  "result": {
    "videoId": "501",
    "durationMs": 734120,
    "status": "unavailable",
    "analysis": {
      "status": "unavailable",
      "data": null,
      "error": {"code": "ANALYZER_NOT_CONFIGURED", "message": "영상 행동 분석기가 아직 연결되지 않았습니다.", "retryable": false}
    },
    "error": null
  },
  "error": null
}
```

파일 문제(같은 공개 원인이 `result.error` 와 `analysis.error` 에 함께 있고, 검증된 길이가 없으면 `durationMs: null`):

<!-- example: callback-file-error -->
```json
{
  "schemaVersion": "1",
  "jobId": "9f0c1e",
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "status": "unavailable",
  "result": {
    "videoId": "501",
    "durationMs": null,
    "status": "unavailable",
    "analysis": {
      "status": "unavailable",
      "data": null,
      "error": {"code": "VIDEO_FORMAT_UNSUPPORTED", "message": "지원하지 않는 영상 형식입니다. WebM(VP8/VP9) 또는 MP4(H.264)만 지원합니다.", "retryable": false}
    },
    "error": {"code": "VIDEO_FORMAT_UNSUPPORTED", "message": "지원하지 않는 영상 형식입니다. WebM(VP8/VP9) 또는 MP4(H.264)만 지원합니다.", "retryable": false}
  },
  "error": null
}
```

문서 생성 실패(유일한 `failed`):

<!-- example: callback-failed -->
```json
{
  "schemaVersion": "1",
  "jobId": "9f0c1e",
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "status": "failed",
  "result": null,
  "error": {"code": "INTERNAL_ERROR", "message": "영상 분석 서버 내부 오류가 발생했습니다.", "retryable": false}
}
```

### 공개 오류 코드

`retryable: true` 는 "**새 requestId 로** 다시 요청하면 복구될 수 있다"는 뜻이다. 서버가 자동으로 재분석하지 않는다.

| code | retryable | 의미 |
|---|---|---|
| `SOURCE_ACCESS_DENIED_OR_EXPIRED` | true | 403 또는 현재 차단된 원본 호스트. 만료라고 단정하지 않는다 |
| `SOURCE_DOWNLOAD_FAILED` | true | 네트워크 오류, 429/5xx, 리다이렉트, 압축 인코딩 응답 |
| `EXTERNAL_SERVICE_UNAVAILABLE` | true | 외부 의존 서비스 일시 장애 |
| `PROCESSING_TIMEOUT` | true | 다운로드 900초·검사 30초·디코딩 3600초 초과, 워커 임대 반복 만료 |
| `SOURCE_NOT_FOUND` | false | 404 |
| `SOURCE_SIZE_MISMATCH` | false | 실제 바이트 수 ≠ `fileSize` |
| `VIDEO_LIMIT_EXCEEDED` | false | 크기·길이(60분)·해상도(회전 반영 1920×1080)·60fps 초과 |
| `VIDEO_FORMAT_UNSUPPORTED` | false | WebM(VP8/VP9)·MP4(H.264) 이외, 일반 영상 스트림이 정확히 1개가 아님 |
| `VIDEO_DECODE_FAILED` | false | 손상·해석 불가(끝까지 디코딩해서 판단) |
| `INTERNAL_ERROR` | false | 서버 환경·도구 문제(파일 손상으로 보고하지 않음) |
| `ANALYZER_NOT_CONFIGURED` | false | 행동 분석기 미연결 |

### 전달 규칙

- 최대 6회 시도. 실패 후 5초 → 15초 → 60초 → 300초 → 900초 뒤 재시도한다(재시작해도 예산 유지).
- 한 시도의 전체 시간 상한은 15초. `2xx` 는 성공, 네트워크 오류·시간 초과·`408`·`429`·`5xx` 만 재시도한다.
  리다이렉트와 나머지 상태 코드(`401`·`400` 등)는 전달 실패로 끝난다.
- 워커가 시도 직전에 죽으면 실제 HTTP 없이 시도 1회가 소모될 수 있다. 같은 본문이 중복 도착할 수 있다
  (at-least-once). 전달이 실패해도 결과는 바뀌거나 지워지지 않으며 조회로 복구한다.

### 수신측(API 서버) 책임

- `X-Internal-Token` 을 검증한다. 틀린 토큰은 `401` 로 거절한다(재분석을 유발하지 않는다).
- `requestId` 별로 **가장 최근에 요청한 작업의 `jobId`** 만 저장하고, 다른 `jobId`·식별자 불일치·중복 도착은 무시한다(멱등).
- 오래 기다려도 콜백이 없으면 `GET` 으로 확인한다. 이전 `requestId` 를 재사용하지 않는다.

## GET /analysis/video/jobs/{jobId}

<!-- example: snapshot-processing -->
```json
{
  "jobId": "9f0c1e",
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "status": "processing",
  "createdAt": "2026-10-02T00:00:01.250Z",
  "finishedAt": null,
  "callbackStatus": "pending",
  "result": null,
  "error": null
}
```

- `status`: `processing` → `completed`(종료 문서 생성됨, `ready/partial/unavailable` 모두 포함) 또는 `failed`(문서 생성 실패).
- `callbackStatus`: `pending` / `sending` / `delivered` / `failed`. 분석 상태와 독립이다.
- 종료 후 `result` 는 **콜백 본문 전체**(실패 콜백 포함)다. 실패 시 `error` 는 `result.error` 와 같다.
- 날짜는 UTC ISO 8601 문자열. `404 JOB_NOT_FOUND`(모르는 작업), `410 JOB_EXPIRED`(보관 만료).

<!-- example: snapshot-failed -->
```json
{
  "jobId": "9f0c1e",
  "requestId": "video-request-001",
  "sessionId": "b1c2d3",
  "status": "failed",
  "createdAt": "2026-10-02T00:00:01.250Z",
  "finishedAt": "2026-10-02T00:03:10.000Z",
  "callbackStatus": "delivered",
  "result": {
    "schemaVersion": "1",
    "jobId": "9f0c1e",
    "requestId": "video-request-001",
    "sessionId": "b1c2d3",
    "interviewId": "42",
    "userId": "7",
    "status": "failed",
    "result": null,
    "error": {"code": "INTERNAL_ERROR", "message": "영상 분석 서버 내부 오류가 발생했습니다.", "retryable": false}
  },
  "error": {"code": "INTERNAL_ERROR", "message": "영상 분석 서버 내부 오류가 발생했습니다.", "retryable": false}
}
```

## 보관

| 대상 | 기간 | 이후 |
|---|---|---|
| 종료 결과(조회·콜백 본문) | 종료 후 90일 | `GET`·같은 `POST` 는 `410`, 미전달 콜백도 함께 폐기 |
| 식별 기록(jobId·키·요청 지문) | 그 뒤 90일 | `GET` 은 `404`, 같은 키로 새 접수 가능 |
| 서버 로컬 영상 사본 | 종료 후 24시간 | 삭제(S3 원본은 삭제하지 않음) |

만료는 UTC 기준 `now >= 만료 시각` 부터이며 정리 작업이 늦어도 응답은 기간대로 바뀐다. 처리 중 작업은 만료되지 않는다.
