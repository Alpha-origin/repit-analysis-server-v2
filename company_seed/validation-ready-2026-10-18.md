# 실행 준비·심사 시연 검증 기록

2026-10-18 · 로컬 검증. 코드 동작, 실수집 접근성, 가상 데모, 실모델 의미 정확도를 구분한다.

## 코드와 재현 검증

- 전체 테스트 **812 passed / 1 skipped**. 기업 도구 테스트 **73 passed**(실제 Chromium 통합 시험 포함).
- src/tests/company_seed Ruff·format·mypy·import-linter 통과. 레포 전체 Ruff는 기존 evaluation 오류 43개로 실패하며 해당 파일은 변경하지 않았다.
- 실제 Chromium으로 JS 본문 생성과 등록 호스트/GET 제한을 시험했다. 이 통합 시험의 HTTP는 MockTransport를 사용해 외부 요청 없이 재현했다.
- 실제 SDK는 HTTP 대역으로 도구 호출·토큰 기록·호출 상한·키 미노출을 시험했다. DART는 ZIP/기업개황 응답 대역으로 정확/다중/미일치·캐시·키 미기록을 시험했다.
- RSS 본문 탐색·XML entity 거부·수동 원문 provenance·중단 복구·기업 시간 상한·기존 산출물 보호·검수 선언·SAMPLE 승인/export 거부를 시험했다.

## SAMPLE 데모 실실행

- `pipeline demo`, `demo --resume`, `summary`, SAMPLE export 차단을 실제 CLI로 실행했다.
- SAMPLE_DEMO, 문서 1개·사실 3개·실제 모델 요청 0, 재개 시 기존 결과 유지. export는 실패하고 목적지 폴더를 만들지 않았다.
- 데모 처리 시간 약 0.003초는 이 기계의 결정적 대역 실행 기록이다. 실모델·실사이트 속도나 정확도로 해석하지 않는다.

## 26개 실수집 재점검

- 수집 성공 7 / 부분 성공 14 / 본문 없음 5, 문서 버전 128개.
- 기록 범위: 실제 공개 사이트의 원문 수집 단계. 문서 용도·필수 사실 충족·사람 검수는 별도다. 원문 버전 수에는 식별 참고 페이지 등도 포함되며 개인정보/약관은 서비스·인재상 사실의 근거로 사용할 수 없도록 추출에서 제한한다.
- 이 실행에서 현대오토에버 회사 소개의 browser 수집을 확인했다. 초기 HTTP 전용 점검은 81개 문서 버전이었다. 탐색 범위가 확장되어 두 실행의 성공률을 같은 조건의 성능 비교로 해석하지 않는다.
- 회사별 처리 시간 합계 477.396초. 단일 시점·단일 기계·네트워크 조건의 관측값이며 반복 성능 벤치마크가 아니다.
- 유료 모델 요청 0, 모델 추출 패키지 0. 기존 시범 초안 4개와 추가 초안 미작성 22개·사람 검수 완료 0개 상태는 유지한다.

| 기업 | 결과 | 문서 버전 | 방식 | 실패 코드 |
|---|---|---:|---|---|
| 삼성SDS | 수집 성공 | 4 | http, xml | — |
| LG CNS | 수집 성공 | 11 | http, xml | — |
| 현대오토에버 | 수집 성공 | 10 | browser, http, xml | — |
| 포스코DX | 수집 성공 | 12 | http | — |
| CJ올리브네트웍스 | 본문 없음 | 0 | — | ROBOTS_DENIED |
| 네이버 | 부분 성공 | 2 | http | ROBOTS_UNAVAILABLE |
| 카카오 | 부분 성공 | 10 | http, xml | ROBOTS_UNAVAILABLE |
| 라인플러스 | 부분 성공 | 9 | http | HTTP_403 |
| 쿠팡 | 수집 성공 | 11 | http, xml | — |
| 우아한형제들 | 본문 없음 | 0 | — | BODY_OR_HOST |
| 당근 | 부분 성공 | 9 | http, xml | BODY_OR_HOST |
| 토스 | 부분 성공 | 8 | http, xml | BODY_OR_HOST |
| 카카오뱅크 | 부분 성공 | 1 | http | ROBOTS_DENIED |
| 카카오페이 | 부분 성공 | 7 | http, xml | BODY_OR_HOST |
| 넥슨 | 본문 없음 | 0 | — | BODY_OR_HOST, ROBOTS_UNAVAILABLE |
| 엔씨소프트 | 부분 성공 | 1 | http | ROBOTS_UNAVAILABLE |
| 넷마블 | 부분 성공 | 7 | http | ROBOTS_UNAVAILABLE |
| 크래프톤 | 본문 없음 | 0 | — | ROBOTS_UNAVAILABLE |
| 스마일게이트 | 부분 성공 | 1 | http | ROBOTS_UNAVAILABLE |
| 야놀자 | 본문 없음 | 0 | — | ROBOTS_UNAVAILABLE |
| 무신사 | 부분 성공 | 2 | http | ROBOTS_UNAVAILABLE |
| 컬리 | 부분 성공 | 1 | http, xml | ROBOTS_UNAVAILABLE |
| 쏘카 | 수집 성공 | 7 | browser, http, xml | — |
| 카카오모빌리티 | 부분 성공 | 1 | http | ROBOTS_UNAVAILABLE |
| NHN | 부분 성공 | 3 | http, xml | BODY_OR_HOST |
| 안랩 | 수집 성공 | 11 | http, xml | — |

## 미실측 범위

- 현재 터미널의 ANTHROPIC_API_KEY·DART_API_KEY는 미설정이다. 실제 모델 추출·API 식별 요청은 실행하지 않았다.
- 실모델 의미 정확도·토큰 비용·기업별 필수 정보 충족률은 미측정이다. 모의 결과를 이 지표로 대체하지 않는다.
- 사람의 자료 검수·사용 적합성 확인·검수 패키지 API DB 반영은 미완료다. 프로그램 구현과 별도 운영 단계다.
- 외부 robots/접근 실패는 정책을 우회하지 않는다. 허용된 대체 출처나 사람이 확보한 원문으로 보완할 수 있다.

원문·구절·상세 보고서는 Git 제외 .cache에 보존한다. 공개할 수 있는 요약·코드·가상 자료만 이 검증 문서에 기록했다.
