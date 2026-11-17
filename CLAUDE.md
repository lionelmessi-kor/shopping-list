# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

Streamlit으로 만든 단일 파일(`app.py`) 쇼핑리스트 웹 앱. 아이템 추가·수정·삭제·체크가 핵심이다. 문서와 사용자 소통은 한국어다.

- `PRD.md`: 요구사항 기준. 구현이 PRD와 맞는지가 검토 기준이다.
- `PROMPTS.md`: 개발 4단계 프롬프트 (뼈대 → 기능 → UI → Playwright 검토). 1~4단계와 그 후 수정까지 완료된 상태다.
- 배포는 아직 하지 않았다(Streamlit Community Cloud 예정).

## 명령어

```
pip install -r requirements.txt   # streamlit만 필요
streamlit run app.py              # 로컬 실행 (기본 http://localhost:8501)
```

린트·테스트 스위트는 없다. 검증은 두 가지 방식으로 한다.
- 로직: `streamlit.testing.v1.AppTest.from_file("app.py")`로 위젯을 조작한다. 추가 입력은 `at.text_input(key="add_name")`, 아이콘 버튼은 `b.help == "수정" | "삭제"`로 찾는다.
- 화면: Playwright로 실제 브라우저를 조작한다.

## 아키텍처

`app.py` 한 파일에 저장 계층, 변경 콜백, 렌더링이 순서대로 들어 있다.

- **데이터**: `data/shopping_list.json`(아이템 리스트)이 유일한 진실의 원천이다. 필드는 `id`(uuid), `name`, `quantity`(1~99), `checked`, `created_at`. `save_items()`는 임시 파일에 쓴 뒤 교체한다.
- **동기화(핵심)**: 모든 스크립트 실행(`init_state`)과 모든 변경 함수(`add/update/delete/toggle/clear/start_edit`)가 시작할 때 `_sync()`로 파일을 다시 읽는다. `st.session_state["shopping_items"]`는 그 결과의 사본일 뿐이다. 여러 탭/사용자가 동시에 써도 서로의 변경을 덮어쓰지 않게 하려는 구조이므로, session_state 값을 믿고 저장하는 코드를 새로 만들지 않는다.
- **락과 재시도**: 변경 함수(`add/update/delete/toggle/clear_checked`)는 `@_locked`(`data/shopping_list.lock` 락 파일)로 동기화→수정→저장 전체를 직렬화한다. 파일 읽기·교체는 `_retry()`로 Windows의 일시적 `PermissionError`를 재시도한다. `OSError`를 곧바로 손상으로 취급하면 정상 파일을 `.bak`으로 치워 버리므로, 손상 판정은 JSON 디코딩 오류(`ValueError`)만 쓴다.
- **키 이름**: 리스트 키는 `shopping_items`다. `st.session_state.items`는 dict 메서드와 겹쳐 오류가 난다.
- **파일 검증**: `load_items()`가 `_normalize()`로 항목을 검증한다(수량 범위, 중복 id 재발급, 이름 없는 항목 제거). 파일이 없으면 빈 목록, 손상됐으면 지우지 않고 `data/shopping_list.corrupt-*.json.bak`으로 옮긴 뒤 `KEY_NOTICE`로 화면에 경고한다.
- **콜백 기반 변경**: 추가 폼(`submit_add`), 체크·수정·삭제 버튼은 모두 `on_click`/`on_change` 콜백이다. 콜백은 화면을 그리기 전에 실행되기 때문에, 진행률 같은 위쪽 요소가 변경 직후에도 맞다. 폼 처리를 본문(`if submitted:`)으로 옮기면 한 박자 늦는 버그가 생긴다.
- **id 기반 위젯**: 아이템은 인덱스가 아니라 `id`로 식별하고 위젯 key도 `chk_<id>`, `edit_<id>` 식으로 만든다. 체크박스 값은 렌더링 직전에 `st.session_state["chk_<id>"]`를 파일 값으로 덮어써서 다른 탭의 변경을 반영한다(위젯 생성 후에는 못 바꾸고, `value=` 인자와 같이 쓰면 경고가 난다).
- **수정 모드**: `st.session_state.editing_id` 하나로 관리하고 한 번에 한 아이템만 수정한다. 다른 곳에서 그 아이템이 삭제되면 `_sync()`가 해제한다.
- **표시 안전**: 이름은 `md_escape()`로 이스케이프해서 체크박스 라벨(마크다운)에 넣는다. 마크다운 특수문자와 URL/이메일 자동 링크(`http`, `www`, `@` 뒤 U+200B 삽입)를 막는다. `unsafe_allow_html`은 고정 CSS와 빈 목록 안내에만 쓰고 사용자 입력을 절대 넣지 않는다.
- **필터**: `st.segmented_control`(좁은 화면에서도 한 줄). 선택을 해제하면 `None`이 오므로 `or "전체"`로 되돌린다.
- **테마/CSS**: `.streamlit/config.toml`(초록 테마)과 `app.py`의 `CSS` 문자열. 이 Streamlit 버전에는 `stVerticalBlockBorderWrapper` testid가 없으므로 그 선택자에 의존하지 않는다. 좁은 화면에서도 행이 쌓이지 않도록 `stHorizontalBlock`에 `flex-wrap: nowrap`을 주고, 아이콘 버튼은 44px 터치 영역을 확보한다.

## 테스트할 때 주의

- **`data/shopping_list.json`은 사용자가 쓰는 실제 데이터일 수 있다.** 실행 중인 앱(8501)에 테스트 항목을 넣거나 지우지 않는다. 앱 복사본(`app.py`, `.streamlit/`)을 임시 폴더에 두고 다른 포트(예: 8502)로 띄워서 테스트하고, 끝나면 서버를 끈다.
- Playwright: 체크박스 `<input>`은 숨겨져 있으므로 `[data-testid="stCheckbox"]`를 클릭한다. 툴팁(`help=`) 때문에 같은 아이콘 버튼이 크기 0으로 한 번 더 존재하므로 `button:visible`로 거른다. `fill()`은 `max_chars`를 우회하므로 길이 제한은 실제 타이핑으로 확인한다.

## 제약

- Streamlit Community Cloud는 재시작·재배포 때 로컬 파일이 초기화될 수 있고, 모든 방문자가 같은 JSON을 공유한다. 영구 저장이나 사용자별 분리는 외부 DB가 필요하며 PRD의 v1 범위 밖이다.
- 로그인, 다중 리스트, 가격 합산, 카테고리도 v1 범위 밖이다.
- 락은 한 서버의 프로세스 간에만 유효하다. 서버가 여러 대로 늘어나면 DB가 필요하다.
