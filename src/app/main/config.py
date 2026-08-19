from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    SERVICE_NAME: str = "analysis-question-server"  # 서비스 표시 이름 (FastAPI title 에 사용)
    VERSION: str = "0.1.0"  # 빌드 버전. 응답 헤더·문서에 표기.
    DEBUG_MODE: bool = False  # True 면 FastAPI debug 모드(상세 에러 트레이스 노출).
    LOGGING_LEVEL: str = "INFO"  # 루트 로거 레벨. DEBUG 시 단계별 풀 데이터 로깅 활성화.
    ROOT_PATH: str = ""  # 리버스 프록시 뒤에 있을 때의 prefix.


def load_app_settings() -> AppSettings:
    return AppSettings()


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", env_file=".env", extra="ignore")

    # 필수. 비어있으면 부팅 시 ValidationError 로 막힌다(운영 사고 방지).
    API_KEY: str

    # LLM 코드 탐색(tool use) + 이미지 2차 LLM 트리아지에서 사용.
    # 추론력과 tool use 안정성을 모두 요구하는 호출에 쓰인다.
    TEXT_MODEL: str = "claude-sonnet-4-6"

    # 이미지 구조화 비전 호출에서 사용. 비전 입력을 지원하는 모델이어야 함.
    VISION_MODEL: str = "claude-sonnet-4-6"


def load_anthropic_settings() -> AnthropicSettings:
    return AnthropicSettings()


class InterviewQaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTERVIEW_QA_", env_file=".env", extra="ignore")

    # ---------------- 텍스트 노이즈 제거 (PDF 추출 직후) ----------------
    # 전체 페이지 중 이 비율 이상의 페이지에서 동일하게 등장하는 줄은 머리/꼬리로 간주해 제거한다.
    HEADER_FOOTER_MIN_RATIO: float = 0.5
    # 문서 앞쪽 이 개수의 페이지에서만 목차 패턴(점선 + 페이지번호) 을 찾는다.
    TOC_FRONT_PAGES: int = 2

    # ---------------- 이미지 트리아지 (규칙 기반, 비전 호출 없음) ----------------
    # 가로/세로 중 짧은 변이 이 픽셀 이하인 이미지는 장식으로 보고 제거한다.
    IMG_TRIAGE_MIN_PX: int = 100
    # 종횡비(긴변/짧은변)가 이 값을 초과하면 구분선·바코드 류로 보고 제거한다.
    IMG_TRIAGE_MAX_RATIO: float = 10.0
    # 페이지 면적 대비 이미지 면적 비율이 이 값 미만이면 장식으로 제거한다. (1% = 0.01)
    IMG_TRIAGE_MIN_AREA_RATIO: float = 0.01
    # 트리아지 통과 이미지 수가 이 값 이상이면 이미지 구조화 분기로 진입한다.
    INFO_IMG_THRESHOLD: int = 3

    # ---------------- LLM 코드 탐색 세션 상한 ----------------
    # read_files ↔ assistant 왕복 최대 횟수. 도달 시 결과를 강제 생성한다.
    MAX_TURNS: int = 8
    # 누적 input 토큰 상한. 초과 시 "더 읽지 말고 결과 만들어라" 지시를 push.
    TOKEN_LIMIT: int = 100_000
    # read_files 로 전달되는 파일 하나의 최대 바이트. 초과 시 잘라서 일부 표시.
    MAX_FILE_BYTES: int = 60_000
    # read_files 1회 요청에서 허용하는 파일 개수 상한.
    MAX_FILES_PER_CALL: int = 12
    # 최종 산출물 질문 수(고정).
    QUESTION_COUNT: int = 5
    # 4단계 세션의 매 호출당 응답 토큰 상한. generate_result 가 들어갈 수 있게 넉넉히.
    STAGE4_RESPONSE_MAX_TOKENS: int = 4096

    # ---------------- LLM 이미지 트리아지 (image_heavy 분기 전용) ----------------
    # 2차 LLM 트리아지 호출 시 응답 토큰 상한.
    LLM_TRIAGE_MAX_TOKENS: int = 2048
    # 각 이미지의 컨텍스트로 가져갈 주변 텍스트 — 이미지 bbox 위/아래로 이 픽셀(포인트) 거리 안의 블록.
    LLM_TRIAGE_CONTEXT_VERTICAL_PX: float = 120.0
    # 이미지 한 개당 컨텍스트로 보낼 텍스트의 최대 문자 수(프롬프트 비대화 방지).
    LLM_TRIAGE_CONTEXT_MAX_CHARS: int = 500

    # ---------------- 비전 호출 동시성/총량 상한 ----------------
    # asyncio.Semaphore 값. 동시에 진행되는 비전 호출 수 제한.
    # rate limit 안전성을 위해 보수적으로 3.
    VISION_CONCURRENCY: int = 3
    # 한 /generate 요청당 비전 호출 총 상한. 초과한 이미지는 구조화 없이
    # 좌표·캡션만으로 본문에 끼워 넣는다. 비용 폭증 차단용.
    VISION_MAX_IMAGES_PER_REQUEST: int = 15
    # 비전 모델 입력 시 긴 변을 이 픽셀로 리사이즈. 토큰 절감 목적.
    VISION_RESIZE_LONG_EDGE_PX: int = 1500
    # 비전 구조화 호출 응답 토큰 상한(image_type/summary/tech_signals 스키마는 작음).
    VISION_STRUCTURING_MAX_TOKENS: int = 1024

    # ---------------- 외부 I/O timeout (초) ----------------
    # 포트폴리오 PDF 다운로드 timeout. 큰 PDF 도 받을 수 있게 넉넉히.
    PDF_DOWNLOAD_TIMEOUT_SECONDS: int = 30
    # GitHub repo 메타 조회 timeout. 단순 GET 이라 짧게.
    GITHUB_API_TIMEOUT_SECONDS: int = 15
    # GitHub tarball 다운로드 timeout. 모노레포 대비 넉넉히.
    TARBALL_TIMEOUT_SECONDS: int = 60

    # ---------------- 콜백(웹훅) 설정 ----------------
    # 콜백 POST 1회 호출의 timeout. 너무 길면 백그라운드 작업이 콜백에 묶임.
    WEBHOOK_TIMEOUT_SECONDS: int = 15
    # 첫 시도 실패 시 재시도까지 대기 시간(초). 일시적인 네트워크 장애 회피용.
    WEBHOOK_RETRY_DELAY_SECONDS: int = 5


def load_interview_qa_settings() -> InterviewQaSettings:
    return InterviewQaSettings()


class FeedbackSoloSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FEEDBACK_SOLO_", env_file=".env", extra="ignore")

    # ---------------- 채점 LLM 호출 ----------------
    # 전 문항을 1회 호출로 채점한다. 15문항 기준 예상 출력 8~10k 토큰이라 여유를 둔 값.
    # 어댑터가 논스트리밍(messages.create) 이라 16k 부근에서 SDK 타임아웃 가드에 걸린다.
    # 이 위로 올려야 하면 어댑터에 스트리밍 경로를 먼저 추가할 것.
    GRADING_MAX_TOKENS: int = 12288
    # 답변 하나가 프롬프트를 잠식하는 것 방지. 초과분은 잘라서 "(이하 생략)" 을 붙인다.
    ANSWER_MAX_CHARS: int = 3000

    # ---------------- 자주 사용한 단어 집계 ----------------
    # 종합 피드백에 실을 상위 단어 개수.
    FREQUENT_WORD_TOP_N: int = 10
    # 이 횟수 미만으로 등장한 단어는 "자주 사용한" 것으로 보지 않는다.
    FREQUENT_WORD_MIN_COUNT: int = 2


def load_feedback_solo_settings() -> FeedbackSoloSettings:
    return FeedbackSoloSettings()


class QuestionTailorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUESTION_TAILOR_", env_file=".env", extra="ignore")

    # ---------------- 질문 재작성 LLM 호출 ----------------
    # 전 문항을 1회 호출로 재작성한다. 5문항 x 본문 한두 줄이면 충분한 값.
    # 부족하면 tool_use JSON 이 잘려 파싱에 실패하고 원질문 폴백으로 떨어진다.
    REWRITE_MAX_TOKENS: int = 2048
    # 원질문·모범답안 하나가 프롬프트를 잠식하는 것 방지. 초과분은 잘라서 "(이하 생략)" 을 붙인다.
    QUESTION_MAX_CHARS: int = 800


def load_question_tailor_settings() -> QuestionTailorSettings:
    return QuestionTailorSettings()
