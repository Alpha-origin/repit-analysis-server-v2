from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    # 소켓/API 서버는 camelCase 를 쓰고 파이썬은 snake_case 를 쓴다.
    # alias_generator 로 두 표현을 잇고, populate_by_name 으로 파이썬 이름 생성도 허용한다.
    # 이 모델을 상속한 응답은 반드시 model_dump(by_alias=True) 로 덤프해야 camelCase 로 나간다.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
