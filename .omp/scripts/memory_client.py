import sys
import json
import urllib.request
import os
from typing import Any, Optional

DAEMON_URL = "http://localhost:8016"

def make_request(endpoint: str, data: Optional[dict[str, Any]] = None, method: str = "POST") -> str:
    url = f"{DAEMON_URL}/{endpoint}"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    if data is not None:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method=method
        )
    else:
        req = urllib.request.Request(
            url,
            headers=headers,
            method=method
        )
    output: str
    try:
        with urllib.request.urlopen(req) as response:
            result = response.read().decode('utf-8')
    except Exception as e:
        output = json.dumps({"status": "error", "message": f"데몬 서버 통신 오류: {e}"}, ensure_ascii=False)
    else:
        output = result
    return output


def _exitcode_from_status(parsed: Any) -> int:
    """
    데몬 응답(또는 make_request 가 만든 통신 오류 응답)의 status 필드를 셸
    종료 코드로 변환합니다. status 가 "error" 이면 1, 그 밖에는 0 입니다.

    이전 구현은 finally 블록에서 return 했습니다. finally 의 return 은 진행
    중인 예외와 앞선 return 값을 모두 덮어쓰므로(파이썬 3.14 의
    SyntaxWarning 사유), 모든 실패가 종료 코드 0 으로 보고되어 just
    레시피가 데몬 장애를 성공으로 통과시켰습니다.
    """
    if isinstance(parsed, dict) and parsed.get("status") == "error":
        return 1
    return 0

def _save() -> int:
    if len(sys.argv) != 6:
        print("사용법: python3 memory_client.py save <session_id> <role> <content|-> <tags>")
        print("  content 가 '-' 이면 표준입력에서 읽습니다. 셸 인용/길이 문제를")
        print("  피해야 하는 긴 본문에는 이 방식을 쓰십시오.")
        return 1

    # '-' 는 표준입력에서 본문을 읽으라는 관례적 표시입니다. just 의
    # "{{CONTENT}}" 인터폴레이션은 값 내부의 따옴표나 매우 긴 본문을
    # 안전하게 전달하지 못하므로, 그런 내용은 stdin 으로 넘깁니다.
    content = sys.argv[4]
    if content == "-":
        content = sys.stdin.read()

    payload = {
        "session_id": sys.argv[2],
        "role": sys.argv[3],
        "content": content,
        "tags": sys.argv[5]
    }
    raw_response = make_request("save", payload, "POST")
    print(raw_response)
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        return 1
    return _exitcode_from_status(parsed)

def _search() -> int:
    if len(sys.argv) < 3:
        print("사용법: python3 memory_client.py search <query> [limit]")
        return 1
    payload = {
        "query": sys.argv[2],
        "limit": int(sys.argv[3]) if len(sys.argv) > 3 else 5
    }
    raw_response = make_request("search", payload, "POST")
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        print(raw_response)
        return 1

    if isinstance(parsed, dict) and "results" in parsed:
        print(json.dumps(parsed["results"], ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return _exitcode_from_status(parsed)

def _export() -> int:
    if len(sys.argv) < 3:
        print("사용법: python3 memory_client.py export <filepath>")
        return 1
    filepath = sys.argv[2]
    raw_response = make_request("export", None, "GET")
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        print(f"응답을 JSON 으로 해석할 수 없습니다.\n응답 내용: {raw_response}")
        return 1

    if not (isinstance(parsed, dict) and parsed.get("status") == "success"):
        print(f"추출 실패: {raw_response}")
        return 1

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(parsed.get("records", []), f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[{filepath}] 파일 기록 중 오류 발생: {e}")
        return 1

    print(f"[{filepath}] 파일로 데이터를 성공적으로 추출했습니다.")
    return 0

def _import() -> int:
    if len(sys.argv) < 3:
        print("사용법: python3 memory_client.py import <filepath>")
        return 1
    filepath = sys.argv[2]
    if not os.path.exists(filepath):
        print(f"오류: [{filepath}] 파일을 찾을 수 없습니다.")
        return 1

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[{filepath}] 파일을 읽는 중 오류 발생: {e}")
        return 1

    payload = {"records": records}
    print(f"[{filepath}] 파일에서 {len(records)}개의 레코드를 수입하여 임베딩을 진행합니다. (다소 시간이 소요될 수 있습니다)")
    raw_response = make_request("import", payload, "POST")
    print(raw_response)

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        return 1
    return _exitcode_from_status(parsed)

def _delete() -> int:
    if len(sys.argv) < 3:
        print("사용법: python3 memory_client.py delete <id> [id ...] | session:<SESSION_ID>")
        return 1

    payload: dict[str, Any] = {}
    ids: list[int] = []
    for arg in sys.argv[2:]:
        if arg.startswith("session:"):
            payload["session_id"] = arg[len("session:"):]
        else:
            try:
                ids.append(int(arg))
            except ValueError:
                print(f"오류: '{arg}' 는 정수 id 또는 'session:<SESSION_ID>' 형식이 아닙니다.")
                return 1
    if ids:
        payload["ids"] = ids

    if not payload:
        print("오류: 삭제 조건이 없습니다. id 또는 session:<SESSION_ID> 를 지정하십시오.")
        return 1

    raw_response = make_request("delete", payload, "POST")
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        print(raw_response)
        return 1

    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return 0 if parsed.get("status") == "success" else 1

def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python3 memory_client.py [save|search|export|import|delete] [arguments]")
        return 1

    command = sys.argv[1]
    exitcode: int
    match command:
        case "save":
            exitcode = _save()
        case "search":
            exitcode = _search()
        case "export":
            exitcode = _export()
        case "import":
            exitcode = _import()
        case "delete":
            exitcode = _delete()
        case _:
            print(f"알 수 없는 명령어: {command}")
            exitcode = 1

    return exitcode

if __name__ == "__main__":
    ec: int = main()
    sys.exit(ec)
