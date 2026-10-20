# 심사 시연 진행안 — 약 5분

설명 원문은 [judging-guide.md](judging-guide.md), 실행 상세는 [collection-guide.md](collection-guide.md)다. 시연 전에 company 의존성과 Chromium 설치를 마친다. 아래 `judge-session` 폴더가 이미 있으면 새 이름을 사용한다.

## 0:00 — 문제와 구조 설명

“기업 맞춤 면접에 필요한 서비스·문제 해결 사례·인재상이 여러 사이트와 법인에 흩어져 있습니다. Re:pit은 이 자료를 수집하고, 생성한 사실을 출처 문단까지 연결해 사람이 검수할 수 있는 패키지로 만듭니다.”

설명서의 구성도를 보여 준다. 수집→근거 추출→검수→내보내기를 짚는다.

## 0:40 — 실행 조건 확인

```sh
uv run --group company python -m company_seed pipeline doctor --all
```

“키의 존재 여부와 브라우저 준비 상태만 확인합니다. 키 값은 표시하지 않고 API를 호출하지 않습니다.”

## 1:10 — 키 없는 재현 데모

```sh
uv run --group company python -m company_seed pipeline demo --out company_seed/.cache/runs/judge-session
uv run --group company python -m company_seed pipeline summary --file company_seed/.cache/runs/judge-session/report.json
```

**반드시 먼저 설명:** “지금은 외부 사이트와 모델 대신 가상 SAMPLE 입력·결정적 대역을 사용합니다. 처리 흐름 재현이며 실제 LLM 정확도 시험은 아닙니다.”

SAMPLE_DEMO 모드, 문서 1개, 사실 3개, 실제 모델 요청 0을 보여 준다. 실행 시간은 해당 기계의 데모 처리 시간이며 실 API 속도로 설명하지 않는다.

## 2:00 — 근거와 검수 장치

`drafts/sample-pipeline.json`과 `review/sample-pipeline.md`를 연다.

“각 사실에는 출처가 있고 별도 검수 자료에는 문단 ID와 구절이 있습니다. 코드가 문단 속 구절 존재를 검사하지만 의미 전체와 법인 귀속은 사람이 대조해야 합니다. review는 아직 null입니다.”

## 2:50 — 중단 복구와 공개 차단

```sh
uv run --group company python -m company_seed pipeline demo --out company_seed/.cache/runs/judge-session --resume
```

“완료된 산출물을 덮지 않고 유지합니다.”

```sh
uv run --group company python -m company_seed export --stage reviewed --directory company_seed/.cache/runs/judge-session/drafts --out company_seed/.cache/runs/judge-session-export
```

이 명령은 SAMPLE 자료라 **실패해야 정상**이다. “실제 데이터처럼 승인하거나 서비스용으로 내보내지 못하게 분리했습니다.” 검수자 이름을 임의로 채워 넣지 않는다.

## 3:30 — 실제 실행과 검증 범위

[검증 기록](validation-ready-2026-10-18.md)의 실수집 표를 보여 준다.

“실제 26개 기업도 별도 수집 점검했습니다. 접근 정책·사이트 형식 때문에 부분 성공과 실패가 존재합니다. 이 상태를 보고서에 남기며 임의의 사실로 채우지 않습니다. JS 렌더링은 실제 Chromium으로 확인했고, DART·Claude는 현재 키가 없어 응답 대역으로 계약을 검증했습니다.”

실제 추출을 시연하려면 미리 자기 계정 키·모델 권한·호출 상한을 설정하고 작은 기업 하나로 실행한다. 준비 없이 전체 유료 호출을 시연하지 않는다.

## 4:20 — 면접 연결과 한계

“검수한 기업 패키지를 면접 생성 시 스냅샷으로 고정해 질문의 맥락과 별도 기업 관점 코멘트에 사용합니다. 실제 기업 채용 점수나 합격 가능성을 계산하지 않습니다.”

질문을 받으면 코드 동작 검증, 실수집 접근성, 실모델 의미 정확도, 사람 검수 완료를 구분해 답한다. 현재 실제 기업 초안 검수 완료는 0개이며 데이터의 운영 반영을 완료했다고 설명하지 않는다.
