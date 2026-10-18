# 기업 데이터 파이프라인 실행 안내

2026-10-18. 26개 등록 기업의 공개 자료 수집 → 법인 식별 보조 → 근거 기반 정보 추출 → 검수 → 패키지 내보내기를 실행한다. 사람 검수 전 데이터는 서비스에 반영하지 않는다. 심사 설명은 [judging-guide.md](judging-guide.md)를 참고한다.

## 1. 설치와 사전 점검

저장소 루트에서 실행한다. 이 작업의 로컬 체크아웃은 `/Users/maple/Repit/work/company-dataset`이다.

```sh
cd /Users/maple/Repit/work/company-dataset
uv sync --frozen --group company
uv run --group company playwright install chromium
uv run --group company python -m company_seed pipeline doctor --all
uv run --group company python -m company_seed pipeline plan --all
```

`doctor`는 키 존재 여부·모델 설정·Chromium 설치만 검사한다. 키를 출력하거나 실 API를 호출하지 않는다. Linux에서는 Chromium 실행 라이브러리도 필요할 수 있으므로 `playwright install --with-deps chromium`을 쓴다. 브라우저를 설치하기 어려우면 `--render never`로 HTTP/PDF 수집만 실행할 수 있다.

`companies.csv`가 26개 대상 목록, `sources.json`이 기업별 URL·허용 호스트·법인 경계다. `assetHosts`는 JS 렌더링에 필요한 별도 정적 자원 호스트이며 필요한 경우에만 명시적으로 등록한다. 시범의 `verified=true`는 URL을 읽어본 조사 이력이며 사람 검수 완료나 현재 접근 성공을 뜻하지 않는다.

## 2. 심사 현장 데모 — 키·외부 네트워크·모델 비용 없음

```sh
uv run --group company python -m company_seed pipeline demo --out company_seed/.cache/runs/judging-demo
uv run --group company python -m company_seed pipeline summary --file company_seed/.cache/runs/judging-demo/report.json
uv run --group company python -m company_seed pipeline demo --out company_seed/.cache/runs/judging-demo --resume
```

가상의 SAMPLE 기업과 HTTP/모델 대역으로 동일 수집·근거 검증·패키지 조립·보고서 경로를 재현한다. `mode=SAMPLE_DEMO`, `sourceType=SAMPLE`, `modelRequestsStarted=0`으로 표시된다. 실제 기업 수집·실제 LLM 추출·의미 정확도 측정 결과가 아니다. SAMPLE은 approve와 실제 export에서 거부한다.

결과의 `drafts/sample-pipeline.json`, `review/sample-pipeline.md`, `report.json`, `run.json`을 보여 줄 수 있다. 이 데모의 데이터는 외부 회사 원문을 포함하지 않는다.

## 3. 실제 수집 — 모델 비용 없음

```sh
uv run --group company python -m company_seed pipeline collect --company toss --out company_seed/.cache/runs/toss-collect
uv run --group company python -m company_seed pipeline collect --all --out company_seed/.cache/runs/all-collect
uv run --group company python -m company_seed pipeline summary --file company_seed/.cache/runs/all-collect/report.json
```

등록 URL부터 방문하며 관련 링크, RSS/Atom·사이트맵의 본문 URL, robots의 등록된 사이트맵을 제한 탐색한다. 피드 제목·요약은 사실 근거로 쓰지 않고 글 본문을 읽는다. HTML 본문이 부족한 HTTP 200 페이지에는 Chromium을 사용한다. robots 차단·403·로그인·접근 확인 화면은 우회하지 않는다.

페이지 보고서는 본문 버전·수집 방식(http/browser/manual/fixture)·실패 코드(ROBOTS_DENIED/ROBOTS_UNAVAILABLE/HTTP_403/NETWORK 등)·상한 도달을 담는다. 본문 캐시는 `.cache/pipeline/<code>/`, 마지막 목록은 `latest.json`에 있다. 중간 진행도 저장하므로 시간 상한/중단 시 확보한 자료를 확인할 수 있다.

## 4. 실제 추출 — 키·모델 필요, 비용 발생

터미널 환경에 `ANTHROPIC_API_KEY`를 설정한다. 이 도구는 다른 프로젝트의 .env나 키 저장소를 자동 탐색하지 않는다. 선택한 계정에서 사용할 수 있는 모델 ID를 명시한다. 아래 예시는 현재 프로젝트 기본 모델이다.

```sh
export COMPANY_SEED_MODEL='claude-sonnet-4-6'
uv run --group company python -m company_seed pipeline extract --company toss --max-model-calls 1 --out company_seed/.cache/runs/toss-extract
```

수집과 추출을 한 번에 실행하려면:

```sh
uv run --group company python -m company_seed pipeline run --all --max-model-calls 26 --out company_seed/.cache/runs/all-run
```

모델 호출 기본 상한은 **1회**다. 전체 실행에는 원하는 상한을 명시한다. 캐시 적중은 상한을 소모하지 않으며, 시도한 요청은 실패해도 소모한다. 모델 자동 재시도는 하지 않는다. 미확인 비용을 0으로 단정하지 않고 시작한 요청 수·확인된 입력/출력 토큰을 기록한다. 26개가 등록되어 있어도 자료 부족·접근 실패·근거 오류가 있는 기업은 초안 생성에 실패할 수 있다.

DART 식별 보조는 `DART_API_KEY` 환경변수가 있을 때만 동작한다. 고유번호 디렉터리/기업개황에서 정규화한 법인명의 **유일한 정확 일치**만 채택한다. 유사·동명·불일치·API 실패는 식별자를 비워 두며 중단 사유와 구분한다. `--no-dart`로 끌 수 있다. 브랜드와 법인명이 다르면 registry의 legalName을 확인해 넣는다. dartCorpCode를 직접 지정해도 법인명 일치가 필요하다. 규모·모회사는 자동 추정하지 않는다.

DART 출처 URL은 키를 제외한 API 리소스 주소다. 같은 corp_code로 자기 키를 넣어 조회하거나 로컬 캐시를 확인해야 하며, 이 주소만 익명 브라우저에 넣으면 인증 오류가 날 수 있다.

### 추출이 failed인 경우

터미널의 원인과 `<out>/report.json`의 `error`를 확인한다. `partial` 수집에도 본문이 있으면 추출할 수 있다. 사용자 실제 실행에서는 toss 본문 8개가 저장됐지만 메인 페이지의 `static.toss.im` 자원 차단으로 수집은 partial이었고, 추출은 모델이 facts를 문자열로 반환하고 요약이 600자를 초과해 실패했다. API 요청 성공과 검증된 초안 생성 성공은 다르며 실패한 요청도 토큰을 사용할 수 있다.

v3는 strict tool use로 배열·필수 필드를 강제하고 요약은 300자 이내로 요청한다. SDK가 지원하지 않는 길이 제한을 설명으로 변환하므로 로컬 검증은 계속 필요하다. 의미/근거/길이 위반은 저장하지 않으며 자동 유료 재시도나 임의 잘라내기는 하지 않는다. 변경 후 기존 캐시는 그대로 두고 새 `--out`(예: `company_seed/.cache/runs/my-toss-extract-v2`)으로 extract를 실행한다. 프롬프트 버전이 달라 기존 실행의 --resume은 사용할 수 없다.

기술 참고: [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use), [SDK 스키마 변환과 제한](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#how-sdk-transformation-works).

## 5. 중단 후 재개

```sh
uv run --group company python -m company_seed pipeline run --all --max-model-calls 26 --out company_seed/.cache/runs/all-run --resume
```

명령·기업 목록·회사 설정·모델·프롬프트 버전·캐시·모드·렌더링/DART 사용 설정이 같아야 한다. 성공한 기업과 이미 초안이 만들어진 부분 성공 기업은 그대로 둔다. 실패·미처리 기업만 다시 시도한다. 호출 상한은 새 실행 프로세스에서 다시 적용되며 캐시는 재사용한다. 완료된 기업의 자료를 새로 수집/갱신하려면 새 `--out` 폴더로 실행한다. 직접 편집한 산출물은 덮지 않는다.

## 6. 자동 수집 불가 자료를 수동 반입

정책·형식 때문에 자동 수집하지 못한 페이지를 사람이 허용된 방법으로 읽어 본문을 준비한 경우에 사용한다. 원래 URL과 법인/서비스 범위를 남겨 자동 자료와 구분한다. 네트워크 우회 기능이 아니다.

```sh
uv run --group company python -m company_seed pipeline import --company naver --file /절대경로/naver-body.txt --url https://d2.naver.com/helloworld/8113611 --scope '네이버 검색 FE 공개 사례'
```

등록 호스트 내 URL, 25MB 이하 PDF/HTML/TXT/MD만 반입한다. `acquisition=manual`로 기록하며 다음 extract에 포함된다. 이후 같은 URL의 HTTP 수집에 성공하면 그 최신 자료를 우선 사용한다. 이미지 PDF는 이 버전에서 OCR하지 않으므로 사람이 텍스트를 준비해야 한다.

## 7. 검수와 내보내기

실행 결과는 다음과 같다.

```text
<out>/
  run.json              실행 설정·모드·프롬프트 버전
  report.json           기업별 상태·문서 버전·요청 수·토큰·처리 시간
  drafts/<code>.json    서버 schemaVersion 1, review=null
  review/<code>.md      출처·문단·원문 구절·검수 항목
```

원문 포함 검수 메모와 캐시는 로컬 검수용이며 그대로 공개 저장소에 올리지 않는다. 문단 내 구절 일치는 의미 정확성을 보증하지 않는다. 사람이 사실, 현재 법인/서비스, 역사적 사례 범위, 파생 연습 신호, 요약을 대조해야 한다.

```sh
uv run --group company python -m company_seed validate --stage draft --directory company_seed/.cache/runs/toss-extract/drafts
```

실제 검수를 완료한 **사람**이 자신의 이름으로 다음 명령을 실행한다. 아래 reviewer 자리에는 실제 검수자 이름을 넣는다. SAMPLE 데모에는 사용할 수 없다.

```sh
uv run --group company python -m company_seed pipeline approve --file company_seed/.cache/runs/toss-extract/drafts/toss.json --reviewer '실제 검수자 이름' --confirm-reviewed --out company_seed/data/reviewed
uv run --group company python -m company_seed validate --stage reviewed
uv run --group company python -m company_seed export --stage reviewed --out company_seed/.cache/exports/reviewed-v1
```

approve는 검수 선언을 기록하며 실제 검수 행위를 인증하지 않는다. 기존 검수본은 덮지 않는다. 묶음 전체 중복·모회사 연결 등은 validate/export가 검사한다. API DB 반영·면접 연동은 검수된 패키지로 별도 로컬 확인한다.

## 제한·상태 해석

- 새 실행은 존재하지 않는 `--out`을 사용하고, 같은 실행의 복구만 --resume으로 한다.
- `collected`: 방문 범위의 수집 완료, `draft`: 검사한 미검수 초안, `partial`: 일부 수집 실패/기업 시간 상한, `failed`: 본문 없음/추출 실패, `SAMPLE_DEMO`: 가상 재현. partial에도 초안이 있을 수 있다. 부분/실패가 있으면 종료 코드 1이며 다른 기업은 계속한다.
- 기업당 수집 180초·12페이지, 문서 25MB·40,000자, PDF 50쪽, 렌더링 45초·40개 GET 요청, 모델 입력 80,000자·출력 6,000토큰. 상한 도달은 사이트 전체 수집 완료를 뜻하지 않는다.
- 호스트 최소 1초·robots Crawl-delay/Request-rate 반영. 60초 초과 간격·지원하지 않는 시간 창 정책은 미수집. 현대 robots 패턴은 Protego로 해석한다. HTTP 재시도 최대 3회, 리디렉션 최대 4회, 이동 대상 robots도 확인한다.
- Chromium은 독립 임시 세션이며 등록 호스트·등록 asset 호스트의 GET만 허용한다. 로그인/사용자 쿠키/POST/추적 자원·권한 우회는 사용하지 않는다. 차단된 자원 때문에 JS가 동작하지 않으면 실패로 표시한다.
- 네트워크·사이트 개편·이미지 PDF·법인 경계·외부 키의 유효성은 실행 시 확인해야 한다. 주기 실행 서비스와 RAG는 현재 데이터 구축 목적에 포함하지 않는다.
