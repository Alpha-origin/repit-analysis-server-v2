# 기업 데이터 파이프라인 실행 안내

2026-10-18. 기존 26개 후보를 대상으로 하는 로컬 구축 도구다. 기업을 지정해 수집→근거 추출→미검수 초안 생성까지 실행하고, 사람이 대조한 결과만 기존 validate/export를 거쳐 사용한다. 운영 DB나 reviewed를 자동으로 변경하지 않는다.

## 준비

```sh
cd /Users/maple/Repit/work/company-dataset
uv sync --frozen
uv run python -m company_seed pipeline plan --all
```

`companies.csv`가 대상 목록, `sources.json`이 수집 설정이다. 회사별 출처 URL·허용 호스트·법인 범위 메모를 등록한다. 시범 4곳의 20개 URL은 사람이 본문을 읽은 출처이며, 나머지 22곳 URL은 **탐색 시작점**이다. URL 등록은 현재 접근 성공·회사 데이터 완성·사람 검수 완료를 뜻하지 않는다. `verified`는 URL을 시범 조사에서 읽었는지 표시하며 자동 검수 통과 플래그가 아니다.

## 먼저 수집만 실행 (모델 비용 없음)

```sh
uv run python -m company_seed pipeline collect --company toss --out company_seed/.cache/runs/toss-collect-01
uv run python -m company_seed pipeline collect --all --out company_seed/.cache/runs/all-collect-01
```

`--company`는 반복해 여러 기업을 지정할 수 있다. 각 실행의 `--out`은 존재하지 않는 새 폴더여야 한다. 기존 폴더를 삭제하거나 덮지 않는다. 동일 캐시를 재사용하므로 실행 결과 폴더 이름만 바꾸면 재실행할 수 있다.

`report.json`에 기업별 결과와 페이지별 changed/unchanged/failed, 원인, 문서 버전, 페이지 상한 도달 여부를 기록한다. 본문과 문단은 `company_seed/.cache/pipeline/<기업코드>/`에 저장된다. HTML 메뉴 등은 제외하고 PDF는 쪽 번호를 유지한다. 최신 수집 목록을 가리키는 `latest.json`과 이전 본문 버전을 분리한다. 본문이 그대로면 기존 버전·수집일을 유지하며, 이번 확인일은 보고서의 checkedAt이다.

## 모델로 추출

터미널 환경에 `ANTHROPIC_API_KEY`를 설정하고 사용할 모델 ID를 `COMPANY_SEED_MODEL`로 지정한다. 키를 명령 인자·Git·검수 파일에 쓰지 않는다. 도구는 서버 .env 파일을 자동으로 읽지 않는다.

```sh
export COMPANY_SEED_MODEL='claude-sonnet-4-6'
uv run python -m company_seed pipeline extract --company toss --out company_seed/.cache/runs/toss-extract-01
```

예시 모델 ID는 현재 프로젝트 기본값이다. 계정에서 사용할 수 있는 다른 모델 ID를 --model로 명시해도 된다. 캐시에 있는 최신 수집 본문만 사용한다. 회사별 최대 한 번의 모델 요청이며, 실패 시 모델 요청을 자동 재시도하지 않는다. 본문 버전·회사 설정·모델·프롬프트·출력 스키마가 같으면 검증한 추출 캐시를 재사용한다. 캐시가 없으면 모델 호출 비용이 발생한다. 원문 입력은 최대 80,000자, 출력 최대 6,000토큰이다. 문서가 많으면 뒤쪽 문단이 입력에서 빠질 수 있으므로 검수 시 범위를 확인한다. 유료 실호출·의미 정확도·기업당 실제 비용 실측은 개발 검증에 포함하지 않았다.

수집과 추출을 이어서 실행하려면 다음과 같이 한다.

```sh
uv run python -m company_seed pipeline run --company toss --out company_seed/.cache/runs/toss-run-01
uv run python -m company_seed pipeline run --all --out company_seed/.cache/runs/all-run-01
```

실행 결과:

```text
<out>/
  report.json          기업별 성공·부분 성공·실패, 사용 토큰
  drafts/<code>.json   schemaVersion 1, review는 항상 null
  review/<code>.md     출처·원문 문단·짧은 근거 구절·사람 검수 항목
```

원문이 인용된 `review`와 본문 캐시는 로컬 검수용이다. 그대로 공개 저장소에 커밋하지 않는다. 기존 시범 `data/review`의 요약 메모와 다르다.

## 검수·내보내기

```sh
uv run python -m company_seed validate --stage draft --directory company_seed/.cache/runs/toss-run-01/drafts
```

1. `review/<code>.md`에서 사실·원문 구절·법인/서비스 범위를 대조한다. 코드 검사는 구절이 해당 문단에 있다는 것만 확인하며, 그 구절이 요약 전체를 뒷받침하는지는 사람이 확인해야 한다.
2. 법인명·현재 서비스·과거 사례 범위·파생 신호·요약을 수정/확인한다. 미확인 식별 번호·규모·모회사는 null을 유지한다.
3. 검수 완료한 JSON의 **사본**을 `company_seed/data/reviewed/`로 옮기고 실제 검수자·날짜를 `review.reviewedBy`·`review.reviewedAt`에 기록한다.
4. 검수본 묶음을 검사하고 새 폴더로 출력한다.

```sh
uv run python -m company_seed validate --stage reviewed
uv run python -m company_seed export --stage reviewed --out company_seed/.cache/exports/reviewed-01
```

검수본이 0개면 마지막 두 명령은 실패하는 것이 정상이다. API에 반영하기 전 목록·스냅샷·질문 전달을 로컬에서 별도로 확인한다.

## 결과와 제한

- `plan`은 네트워크·모델 호출을 하지 않는다. `collect`는 HTTP만, `extract/run`은 새 추출이 필요할 때 모델을 호출한다.
- status `collected`: 방문한 범위에서 본문 수집 성공, `draft`: 형식·근거 연결 검사 통과한 미검수 초안, `partial`: 일부 페이지 실패, `failed`: 본문 없음/추출·검증 실패. 부분 성공·실패가 있으면 종료 코드 1이며 다른 기업은 계속 처리한다. partial이어도 검증된 초안이 생성될 수 있으므로 report와 산출물을 함께 본다.
- limitReached=true는 추가 후보가 있지만 방문 상한으로 중단했음을 뜻한다. 도메인 전체를 수집했다는 의미가 아니다.
- 회사당 최대 12페이지, 문서당 25MB, 문서 본문 40,000자, PDF 최대 50쪽. 출처는 명시한 URL부터 방문하고 관련 경로 링크만 제한 탐색한다. robots 정책을 지키고 호스트별 1초 이상 간격을 둔다. 정책이 더 긴 간격을 요구하면 반영하며 60초 초과 정책은 수집하지 않는다.
- HTTP 429/일시적 서버 오류·네트워크 오류는 최대 3회, 리디렉션은 최대 4회. 허용 호스트 밖으로 이동하지 않는다. 리디렉션 대상도 robots 확인. robots 404는 정책 부재, 기타 조회 실패는 차단으로 처리한다.
- JS 전용 페이지·이미지 PDF·인증/봇 차단·미등록 리디렉션 호스트는 사람이 확인해야 한다. 본문이 짧으면 실패한다. 표준 HTML 추출이라 페이지별 정밀 파싱은 보완이 필요할 수 있다.
- DART 자동 법인 매칭, RSS/사이트맵 전용 어댑터, 브라우저 렌더링, 주기 실행, 검색/RAG는 이번 버전에 없다. 최초 출처 등록·법인 구분·최종 검수에는 사람이 관여한다.
- 검수자 기록을 검사해도 실제 검수 행위를 인증하지는 않는다. 이 도구는 사람 검수 이름·날짜를 자동으로 채우지 않는다.
