# 기업 데이터셋 구축

기존 후보 **26개**를 대상으로 하는 별도 데이터 트랙이다. 서버의 샘플 패키지를 교체하거나 운영 DB에 반영하지 않는다. 계약은 작업 공간 `Doc/company-impl-spec/01-company-data.md`의 schemaVersion 1과 API `8623c15`의 CompanyDataPackage를 따른다.

현재는 **시범 4개 초안 / 추가 조사 22개 / 사람 검수 완료 0개**다. 26개 전부 구축 완료라는 뜻이 아니다. `companies.csv`가 확정 대상 목록이며 각 법인 경계 확인 과제도 담고 있다. 네이버·토스(비바리퍼블리카)·삼성SDS·안랩부터 자료 본문과 법인 범위를 확인했다.

## 구조

- `data/drafts/`: AI가 원문을 읽고 요약한 시범 기업 JSON. 검수 기록은 null.
- `data/review/`: 출처별 확인 위치와 fact, 범위 제한, 남은 검수 과제. 사람 검수 기록이 아니다.
- `data/reviewed/`: 사람이 원문·법인 경계·파생 신호를 확인한 뒤 넣는 JSON. 현재 비어 있다.
- `.cache/`: 조사 중 다운로드한 원문 임시 파일. Git 제외. CLI에는 다운로드·유료 API 호출 기능이 없다.

## 실행

레포 루트에서 `uv sync --frozen` 후 실행한다. 서버 프로세스나 API 키는 필요 없다.

```sh
uv run python -m company_seed inventory
uv run python -m company_seed validate --stage draft
uv run python -m company_seed validate --stage reviewed
uv run python -m company_seed export --stage reviewed --out /새/경로/company-packages
```

현재 reviewed 검증과 export는 검수본이 없으므로 실패하는 것이 정상이다. `--directory`로 별도 검수 폴더를 지정할 수 있다. export는 전체 검증을 먼저 수행하며 기존 목적지 폴더를 덮지 않는다. 검사 시점은 한국 날짜를 쓴다. 형식 검사 통과가 원문 사실 확인이나 사람 검수를 증명하지는 않는다. 이 도구는 검수자 이름을 자동으로 채우지 않는다.

작성 규칙은 API 필수 조건보다 엄격하다. 서비스 2~4개, 출처 scope 필수, SAMPLE 제외, 수집일/발행일/검수일 검증, 묶음 내 법인 ID 유일성·모회사 누락/순환을 검사한다. 모회사 부재 시 경고하는 API와 달리 이 도구는 실패한다. 이는 작성 도구의 가정이며 서버 계약을 바꾸지 않는다. 서버에 들어갈 검수자는 실제 검수한 사람을 기록해야 한다.

## 자료 해석과 다음 배치

문화·서비스·기술 사례는 소속 법인과 공개 시점에 한정한다. 과거 사례를 현재 미해결 문제로 제시하지 않는다. 인재상 신호는 면접 연습을 위한 **DERIVED** 해석이며 회사의 채용 채점표가 아니다. 규모·DART 식별자·모회사 근거를 확인하지 못하면 null로 둔다. 토스뱅크·토스증권 등 계열사의 기술을 비바리퍼블리카에 섞지 않는다.

다음 조사 대상은 CSV의 pilot=false 22곳이다. 채용/문화 → 대표 서비스 2~4개 → 공개 기술 사례 본문 순서로 확인하고, 법인 범위가 불명확하면 근거를 보완한다. 26개라는 수를 맞추기 위해 비어 있는 가짜 패키지를 만들거나 회사를 대체하지 않는다. 회사마다 초안을 만든 뒤 검수 메모를 함께 추가한다. 이후 검수자가 원문 대조, 현재 서비스 여부, 법인명/번호, 파생 신호를 확인하고 review를 기록한 사본만 reviewed에 넣는다. reviewed 전체 검사 후 로컬 API 가져오기·목록·스냅샷을 별도로 확인해야 공개 준비가 된다.

## 확인

```sh
uv run ruff check --no-fix src tests company_seed
uv run mypy src tests company_seed
uv run pytest
```

데이터 폴더는 패키지 wheel에 포함하지 않는다. 이 도구는 레포 루트에서 실행하는 오프라인 구축 도구다.
