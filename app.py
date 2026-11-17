"""쇼핑리스트 앱 - 2단계: 추가/수정/삭제/체크 + 저장, 진행률, 필터, 일괄 삭제."""
import functools
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="쇼핑리스트", page_icon="🛒", layout="centered")

DATA_FILE = Path(__file__).parent / "data" / "shopping_list.json"
LOCK_FILE = DATA_FILE.with_suffix(".lock")
MAX_NAME_LEN = 100
FILTERS = ["전체", "미완료", "완료"]

# session_state 키 (주의: "items"는 dict 메서드와 겹치므로 사용 금지)
KEY_ITEMS = "shopping_items"
KEY_EDITING = "editing_id"
KEY_ERROR = "edit_error"
KEY_NOTICE = "load_notice"
KEY_ADD_WARN = "add_warning"


# ---------- 저장/로드 ----------
def _normalize(raw: object, seen: set[str]) -> dict | None:
    """파일에서 읽은 항목을 검증하고 기본값을 채운다. 쓸 수 없는 항목은 None."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        qty = int(raw.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or item_id in seen:
        item_id = str(uuid.uuid4())  # 위젯 key 충돌 방지
    seen.add(item_id)
    created = raw.get("created_at")
    return {
        "id": item_id,
        "name": name.strip()[:MAX_NAME_LEN],
        "quantity": min(max(qty, 1), 99),
        "checked": raw.get("checked") is True,
        "created_at": created if isinstance(created, str) else "",
    }


def _backup_corrupt_file() -> str | None:
    """손상된 데이터 파일을 지우지 않고 .bak으로 옮겨 보존. 보존 파일명을 반환."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = DATA_FILE.with_name(f"shopping_list.corrupt-{stamp}.json.bak")
    try:
        DATA_FILE.replace(bak)
    except OSError:
        return None
    return bak.name


def _retry(fn, attempts: int = 20, delay: float = 0.05):
    """다른 프로세스가 파일을 교체하는 순간(Windows의 PermissionError 등)에 잠깐 재시도."""
    for n in range(attempts):
        try:
            return fn()
        except FileNotFoundError:
            raise
        except OSError:
            if n == attempts - 1:
                raise
            time.sleep(delay)


def _read_json():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def _file_lock(timeout: float = 5.0, stale: float = 10.0):
    """데이터 파일의 읽기-수정-쓰기 구간을 프로세스/탭 간에 직렬화하는 락 파일.

    락이 stale 초보다 오래되면 죽은 락으로 보고 정리한다. timeout 안에 못 얻으면
    저장 자체를 막지 않도록 그냥 진행한다(이 경우 락 파일은 지우지 않는다).
    """
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    owned = False
    while True:
        try:
            os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            owned = True
            break
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > stale:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
    try:
        yield
    finally:
        if owned:
            LOCK_FILE.unlink(missing_ok=True)


def _locked(fn):
    """변경 함수 전체(동기화 → 수정 → 저장)를 락으로 감싼다."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _file_lock():
            return fn(*args, **kwargs)
    return wrapper


def load_items() -> list[dict]:
    """JSON 파일에서 아이템 로드. 파일이 없으면 빈 리스트.

    파일이 손상됐으면 .bak으로 보존하고 빈 리스트로 시작하며, 알림 문구를 남긴다.
    """
    try:
        data = _retry(_read_json)
        if not isinstance(data, list):
            raise ValueError("list가 아님")
    except FileNotFoundError:
        return []
    except ValueError:
        bak = _backup_corrupt_file()
        msg = "데이터 파일이 손상되어 빈 목록으로 시작했어요."
        if bak:
            msg += f" 원본은 data/{bak} 로 보관했어요."
        st.session_state[KEY_NOTICE] = msg
        return []
    seen: set[str] = set()
    items = [_normalize(raw, seen) for raw in data]
    return [i for i in items if i is not None]


def save_items(items: list[dict]) -> None:
    """아이템 리스트를 JSON 파일에 저장 (UTF-8, 한글 유지)."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 임시 파일에 먼저 쓰고 교체해서, 저장 도중 중단돼도 기존 파일이 깨지지 않게 함
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    _retry(lambda: tmp.replace(DATA_FILE))


def _sync() -> None:
    """파일의 최신 내용을 session_state로 가져온다.

    다른 탭/사용자가 저장한 변경을 덮어쓰지 않도록, 화면을 그리기 전과
    모든 변경 작업(추가/수정/삭제/체크) 직전에 호출한다.
    """
    st.session_state[KEY_ITEMS] = load_items()
    editing = st.session_state.get(KEY_EDITING)
    if editing and not any(i["id"] == editing for i in st.session_state[KEY_ITEMS]):
        st.session_state[KEY_EDITING] = None  # 다른 곳에서 삭제된 항목


def init_state() -> None:
    st.session_state.setdefault(KEY_EDITING, None)
    st.session_state.setdefault(KEY_ERROR, None)
    _sync()


# ---------- 기능 ----------
def _items() -> list[dict]:
    return st.session_state[KEY_ITEMS]


def _find(item_id: str) -> dict | None:
    return next((i for i in _items() if i["id"] == item_id), None)


@_locked
def add_item(name: str, quantity: int) -> bool:
    name = name.strip()
    if not name:
        return False
    _sync()
    _items().append(
        {
            "id": str(uuid.uuid4()),
            "name": name,
            "quantity": int(quantity),
            "checked": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save_items(_items())
    return True


@_locked
def update_item(item_id: str, name: str, quantity: int) -> bool:
    name = name.strip()
    _sync()
    item = _find(item_id)
    if not name or item is None:
        return False
    item["name"] = name
    item["quantity"] = int(quantity)
    save_items(_items())
    return True


@_locked
def delete_item(item_id: str) -> None:
    _sync()
    st.session_state[KEY_ITEMS] = [i for i in _items() if i["id"] != item_id]
    if st.session_state[KEY_EDITING] == item_id:
        st.session_state[KEY_EDITING] = None
    save_items(_items())
    st.toast("삭제했어요.", icon="🗑️")


@_locked
def toggle_item(item_id: str) -> None:
    """체크박스 위젯의 현재 값을 아이템에 반영."""
    _sync()
    item = _find(item_id)
    if item is not None:
        item["checked"] = bool(st.session_state.get(f"chk_{item_id}", False))
        save_items(_items())


@_locked
def clear_checked() -> None:
    _sync()
    st.session_state[KEY_ITEMS] = [i for i in _items() if not i.get("checked")]
    editing = st.session_state[KEY_EDITING]
    if editing and _find(editing) is None:
        st.session_state[KEY_EDITING] = None
    save_items(_items())
    st.toast("완료 항목을 삭제했어요.", icon="🧹")


# ---------- 콜백 ----------
def start_edit(item_id: str) -> None:
    _sync()
    item = _find(item_id)
    if item is None:
        return
    st.session_state[KEY_EDITING] = item_id
    st.session_state[KEY_ERROR] = None
    # 수정 입력창의 초기값 설정
    st.session_state[f"edit_name_{item_id}"] = item["name"]
    st.session_state[f"edit_qty_{item_id}"] = int(item.get("quantity", 1))


def cancel_edit() -> None:
    st.session_state[KEY_EDITING] = None
    st.session_state[KEY_ERROR] = None


def save_edit(item_id: str) -> None:
    name = st.session_state.get(f"edit_name_{item_id}", "")
    qty = st.session_state.get(f"edit_qty_{item_id}", 1)
    if not name.strip():
        st.session_state[KEY_ERROR] = "이름을 입력해 주세요."
    elif update_item(item_id, name, qty):
        cancel_edit()
    else:  # 다른 탭에서 이미 삭제된 항목
        cancel_edit()
        st.toast("이미 삭제된 항목이에요.", icon="⚠️")


# ---------- 표시 ----------
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>$&:^])")


_AUTOLINK = re.compile(r"(https?|ftp|www|@)", re.IGNORECASE)


def md_escape(text: str) -> str:
    """사용자 입력이 마크다운으로 해석되지 않도록 특수문자를 이스케이프.

    URL/이메일 자동 링크도 막기 위해 http, www, @ 뒤에 보이지 않는 공백(U+200B)을 넣는다.
    """
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return _AUTOLINK.sub("\\1\u200b", text)


CSS = """
<style>
.block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 640px; }
h1 { color: #1F7A45; letter-spacing: -0.5px; }
/* 좁은 화면에서도 행(체크박스 - 버튼)이 세로로 쌓이지 않게 유지 */
[data-testid="stHorizontalBlock"] { flex-wrap: nowrap; gap: 0.5rem; }
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 0; }
/* 아이템 카드 */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stCheckbox"] label p { font-size: 1.05rem; }
/* 수정/삭제 아이콘 버튼: 터치하기 쉽게 최소 44x44px 확보 */
button[kind="tertiary"] { padding: 0; min-width: 44px; min-height: 44px; }
[data-testid="stColumn"]:has(button[kind="tertiary"]) { flex: 0 0 44px; width: 44px; min-width: 44px; }
.empty-state { text-align: center; padding: 2.5rem 0; color: #6B7280; }
.empty-state .icon { font-size: 3rem; }
</style>
"""


def inject_css() -> None:
    # 고정 문자열만 주입한다 (사용자 입력은 절대 포함하지 않음)
    st.markdown(CSS, unsafe_allow_html=True)


def render_header() -> None:
    total = len(_items())
    done = sum(1 for i in _items() if i.get("checked"))
    st.title("🛒 쇼핑리스트")
    if total:
        st.markdown(f"**{done} / {total}** 완료")
        st.progress(done / total)


def submit_add() -> None:
    """추가 폼 제출 콜백. 화면을 그리기 전에 실행되므로 진행률 등이 바로 반영된다."""
    name = st.session_state.get("add_name", "")
    qty = st.session_state.get("add_qty", 1)
    if add_item(name, qty):
        st.toast("추가했어요.", icon="✅")
    else:
        st.session_state[KEY_ADD_WARN] = "아이템 이름을 입력해 주세요."


def render_add_form() -> None:
    with st.form("add_form", clear_on_submit=True, border=True):
        c1, c2, c3 = st.columns([6, 2, 2], vertical_alignment="bottom")
        c1.text_input("아이템", key="add_name", max_chars=MAX_NAME_LEN, placeholder="무엇을 살까요?")
        c2.number_input("수량", key="add_qty", min_value=1, max_value=99, value=1, step=1)
        c3.form_submit_button("추가", type="primary", use_container_width=True, on_click=submit_add)
    warn = st.session_state.pop(KEY_ADD_WARN, None)
    if warn:
        st.warning(warn)


def render_empty_state() -> None:
    st.markdown(
        '<div class="empty-state"><div class="icon">🧺</div>'
        "<div>아직 담은 물건이 없어요</div>"
        "<div>위에서 첫 아이템을 추가해 보세요</div></div>",
        unsafe_allow_html=True,
    )


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    with st.container(border=True):
        if st.session_state[KEY_EDITING] == item_id:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            c1.text_input("이름", key=f"edit_name_{item_id}", max_chars=MAX_NAME_LEN)
            c2.number_input("수량", min_value=1, max_value=99, step=1, key=f"edit_qty_{item_id}")
            b1, b2 = st.columns(2)
            b1.button("저장", key=f"save_{item_id}", on_click=save_edit, args=(item_id,),
                      type="primary", use_container_width=True)
            b2.button("취소", key=f"cancel_{item_id}", on_click=cancel_edit,
                      use_container_width=True)
            if st.session_state[KEY_ERROR]:
                st.warning(st.session_state[KEY_ERROR])
            return

        qty = int(item.get("quantity", 1))
        name = md_escape(str(item.get("name", "")))
        label = f"{name} × {qty}"
        if item.get("checked"):
            label = f":gray[~~{label}~~]"

        # 파일(다른 탭 포함)의 최신 체크 상태를 위젯에 반영 (위젯 생성 전에만 가능)
        st.session_state[f"chk_{item_id}"] = bool(item.get("checked"))
        c1, c2, c3 = st.columns([8, 1.2, 1.2], vertical_alignment="center")
        c1.checkbox(
            label,
            key=f"chk_{item_id}",
            on_change=toggle_item,
            args=(item_id,),
        )
        c2.button("✏️", key=f"edit_{item_id}", help="수정", on_click=start_edit,
                  args=(item_id,), type="tertiary")
        c3.button("🗑️", key=f"del_{item_id}", help="삭제", on_click=delete_item,
                  args=(item_id,), type="tertiary")


def main() -> None:
    inject_css()
    init_state()

    notice = st.session_state.pop(KEY_NOTICE, None)
    if notice:
        st.warning(notice, icon="⚠️")

    render_header()
    render_add_form()

    if not _items():
        render_empty_state()
        return

    f1, f2 = st.columns([3, 2], vertical_alignment="center")
    view = f1.segmented_control(
        "보기", FILTERS, default=FILTERS[0], selection_mode="single",
        label_visibility="collapsed",
    ) or FILTERS[0]
    f2.button(
        "완료 항목 삭제",
        on_click=clear_checked,
        disabled=not any(i.get("checked") for i in _items()),
        use_container_width=True,
    )

    if view == "미완료":
        shown = [i for i in _items() if not i.get("checked")]
    elif view == "완료":
        shown = [i for i in _items() if i.get("checked")]
    else:
        shown = _items()

    if not shown:
        st.caption("해당하는 아이템이 없어요.")
    for item in shown:
        render_item_row(item)


if __name__ == "__main__":
    main()
