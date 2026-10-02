# API 서버 ↔ AI 서버 음성 분석 통신

음성은 일반 피드백과 별도 요청·별도 콜백으로 처리한다. 기존 `/feedback/solo`,
`/feedback/multi`에는 recordings/interviewVideo를 추가하지 않는다. 영상은 별도 API([video-api.md](video-api.md))로 처리한다.

## 설정

API와 워커에 동일한 설정을 적용한다. 실제 설치·워커 시작 방법은 [audio-analysis.md](audio-analysis.md) 참고.

```dotenv
AUDIO_ENABLED=true
AUDIO_CALLBACK_HOSTS=["api.example.com"]
AUDIO_POLICY__SOURCE_HOSTS=["example-bucket.s3.ap-northeast-2.amazonaws.com"]
```

SOURCE_HOSTS는 와일드카드 없이 실제 S3 호스트를 지정한다. 기본값은 빈 목록이며 원격 입력을 거부한다.
HTTPS, 기본 443 포트, 지정된 S3 호스트만 지원한다. URL 사용자정보·fragment와 리다이렉트는 거부한다.
기존 내부 서비스 인증 게이트웨이 뒤에서 사용한다. 별도 인증 프로토콜은 이번 변경에서 추가하지 않았다.

## POST /analysis/audio

```json
{
  "requestId": "audio-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "callbackUrl": "https://api.example.com/api/analyses/audio/callback",
  "recordings": [{
    "recordingId": "301",
    "questionId": "101",
    "answerId": "201",
    "fileUrl": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/interview-recordings/42/301.mp3?X-Amz-Signature=...",
    "contentType": "audio/mpeg",
    "fileSize": 1048576,
    "uploadedAt": "2026-09-21T07:56:31Z",
    "endReason": "user"
  }]
}
```

- endReason 외 모든 예시 필드 필수. endReason은 user/timeout/interrupted/unknown이며 기본 unknown.
- 식별자는 문자열. uploadedAt은 타임존 포함 ISO 8601.
- recordings는 1~12개이며 recordingId와 answerId는 각각 요청 내 중복 불가.
- 답변별 최종 선택 녹음만 전달한다. 질문/답변/녹음 소유권 관계는 API 서버가 검증한다.
- contentType은 audio/* MIME 형식. 실제 디코딩 가능 여부는 파일 검사 단계에서 확인한다.
- fileSize는 양의 정수 바이트. 최대 100,000,000바이트(서버 정책이 더 작으면 그 제한 적용).
- 기본 재생 길이 제한 240초. 업로드 완료 후 요청한다.
- callbackUrl은 필수이며 서버 허용 목록의 HTTPS URL만 지원한다.

202 응답:

```json
{"jobId":"...","requestId":"audio-request-001","sessionId":"b1c2d3","status":"accepted"}
```

202는 영속 접수 완료이며 다운로드·분석 성공을 의미하지 않는다.
422는 입력 검증 실패, 409는 동일 sessionId/requestId에 다른 내용·정책 사용이다.
통신 실패 시 같은 requestId로 재접수하면 기존 jobId를 반환한다.
새 분석은 새 requestId를 사용한다.

중복 비교 시 Presigned URL의 X-Amz-* 및 서명 인증 파라미터는 제외하지만 객체 경로,
versionId 등 나머지 파라미터와 파일 메타데이터는 비교한다. 객체는 불변이어야 한다.
서명만 갱신해 재접수해도 기존 작업의 URL은 교체하지 않는다.

## 파일 획득·만료

AI 워커가 URL에 직접 GET하고, 제한된 크기로 스트리밍 다운로드한다.
실제 수신 바이트와 fileSize가 다르면 실패한다. 성공한 파일만 SHA-256 기반 내부 저장소로 확정한다.
분석용 WAV도 다운로드된 실제 체크섬을 사용한다.

403은 SOURCE_ACCESS_DENIED_OR_EXPIRED로 반환한다. 403만으로 만료와 권한 오류를 확정하지 않는다.
현재 자동 URL 재발급 API는 연결하지 않았다. 충분한 유효기간을 제공하고, 만료 실패 후에는
새 URL과 새 requestId로 요청한다. 일시적인 네트워크·5xx·429 오류는 제한 재시도한다.
작업 DB에 원본 URL이 저장되므로 접근 권한·보관 정책을 적용한다.

## 콜백 POST {callbackUrl}

모든 답변의 처리가 종료되면 한 번의 결과 묶음을 전달한다. 전송은 재시도될 수 있다.

```json
{
  "jobId": "...",
  "requestId": "audio-request-001",
  "sessionId": "b1c2d3",
  "interviewId": "42",
  "userId": "7",
  "status": "partial",
  "results": [{
    "recordingId": "301",
    "questionId": "101",
    "answerId": "201",
    "status": "partial",
    "durationMs": 120000,
    "timing": {"status": "ready", "data": {}, "error": null},
    "fluency": {"status": "partial", "data": {}, "error": null},
    "error": null
  }]
}
```

위 data는 구조 설명용 자리표시자이며 실제로는 다음 측정 결과가 들어간다. 모든 키는 camelCase다.

- timing.data: version, status, silence, speed, stability, measurementPolicy, limitations, 선택적 reason.
  - silence: speechMs, leadingNonSpeechMs, trailingNonSpeechMs, internalNonSpeechMs,
    internalNonSpeechRatio, internalPauses(startMs/endMs), pauseCount, thresholdedPauseMs.
    무발화 시 wholeFileNonSpeechMs/internalPauses만 제공한다.
  - speed: syllableCount, includingInternalPauses, excludingVadNonSpeech. 단위는 한글 표기 음절/초.
  - stability: windows(startMs/endMs/rate), mean, standardDeviation, coefficientOfVariation, method.
  - 입력 부족한 측정은 null이며 reason에 사유를 제공한다.
- fluency.data: version, status, model, evidenceBasis, observations,
  fillerCandidateCount, repetitionCandidateCount, finalSentence, finalSentenceEvidence,
  comment, acousticStuttering, limitations.
  - observations: kind(filler/repetition/restart/incomplete_sentence), startChar, endChar, quote, explanation.
  - 문자 위치는 원문 전사의 Python 문자열 기준 [startChar, endChar). 전체 전사는 콜백에 포함하지 않는다.
  - acousticStuttering은 현재 unavailable이며, 유창성 결과는 정상 처리돼도 partial이다.

라인 결과가 unavailable이면 data는 null, error는 code/message/retryable 객체다.
파일 error는 전처리 또는 분석 작업 실패 원인을 제공하며 정상적인 부분 측정에서는 null일 수 있다.
미검출·입력 부족은 반드시 기술적 작업 실패를 의미하지 않는다.

ready=요구 결과 제공, partial=일부 결과 제공, unavailable=결과 제공 불가.
모든 답변이 ready일 때 전체 ready, 모두 unavailable일 때 전체 unavailable, 나머지는 partial.
콜백 도착은 처리 종료를 의미하며 partial은 처리 중 상태가 아니다.
retryable은 수정·재요청으로 복구 가능하다는 뜻이며 현재 자동 재시도 중이라는 뜻이 아니다.

실패 코드 예: SOURCE_ACCESS_DENIED_OR_EXPIRED, SOURCE_NOT_FOUND, SOURCE_DOWNLOAD_FAILED,
SOURCE_SIZE_MISMATCH, SOURCE_TOO_LARGE_OR_SIZE_MISMATCH, EXTERNAL_SERVICE_UNAVAILABLE.
message는 표시용이며 분기는 code/status를 사용한다.

API 서버는 결과 저장 후 2xx로 응답하고 jobId 기준 중복 처리한다.
콜백 도착 순서는 보장하지 않으며 이전 requestId의 결과가 최신 결과를 덮어쓰지 않도록 한다.

## GET /analysis/audio/jobs/{jobId}

진행 상태·단계별 오류·콜백 상태·최종 result를 조회한다. result는 콜백 본문과 같다.
status는 processing/completed/failed이며 결과 완전성 ready/partial/unavailable과 별개다.
404는 알 수 없는 jobId다.

기존 /audio/analysis, /audio/jobs/{jobId}는 로컬 입력 호환용으로 남아 있다.
신규 연동은 반드시 /analysis/audio를 사용한다.
