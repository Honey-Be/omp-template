import sys
import re
import json

# ---------------------------------------------------------------------------
# 민감 정보 패턴의 실행 가능한 기준(canonical source)입니다.
#
# .omp/workspace.yml 의 security.privacyScanner.patterns 는 omp 런타임이 직접
# 읽는 사본이므로 이 목록과 반드시 동일하게 유지해야 합니다. 두 목록이
# 어긋나면 한쪽이 탐지한 값을 다른 쪽이 마스킹하지 못해 유출 경로가 생깁니다.
#   드리프트 검사:  just verify-patterns
#
# 패턴 1 은 -----BEGIN ... KEY----- 만 있고 -----END----- 이 잘린 조각까지
# 덮습니다. 대안의 앞가지가 END 까지의 최단 일치를 먼저 시도하고, END 가 없으면
# 뒷가지 `.*` 가 남은 전체를 삼킵니다. 이전 구현은 BEGIN/END 쌍만 매칭했기
# 때문에, workspace.yml 이 BEGIN 만으로 탐지하던 잘린 키가 정작 마스킹되지
# 않고 그대로 통과했습니다.
#
# 대소문자 구분은 전역 플래그 대신 각 패턴의 인라인 `(?i)` 로 표기합니다.
# 어떤 패턴이 대소문자를 무시하는지 정의 자체에 드러나야 하고, workspace.yml
# 사본과 문자열 단위로 비교할 수 있어야 합니다.
# ---------------------------------------------------------------------------
SENSITIVE_PATTERNS = [
    r"(?i)-----BEGIN[ A-Z]*PRIVATE KEY-----(?:.*?-----END[ A-Z]*PRIVATE KEY-----|.*)",
    # 삼중 인용 원시 문자열을 쓰는 이유: 문자 클래스 안의 큰따옴표를 `\"` 로
    # 이스케이프하면 정규식 의미는 같아도 문자열이 달라져, workspace.yml 사본과
    # 문자 단위로 비교하는 just verify-patterns 가 불일치로 잡습니다.
    r"""(?i)(api[_-]?key|secret|token|password)["'\s:=]+([A-Za-z0-9_\-]{16,})""",
    r"(?i)sk-[a-zA-Z0-9]{32,}",
    r"(?i)ghp_[a-zA-Z0-9]{36}",
]

REDACTION = "[REDACTED_BY_PRIVACY_FILTER]"


def scan_and_mask(text: str) -> str:
    """
    텍스트 내의 민감 정보를 감지하고 REDACTION 문자열로 마스킹합니다.

    캡처 그룹이 2개 이상인 패턴은 key=value 형태로 보고 값(그룹 2)만 가립니다.
    키 이름과 구분자는 남겨 두어야 무엇이 걸렸는지 확인할 수 있습니다. 그 밖의
    패턴은 일치 구간 전체를 가립니다.

    그룹 수는 컴파일된 정규식의 groups 속성으로 판단합니다. 이전 구현은 패턴
    문자열에 "()" 또는 "(?i)(" 가 들어 있는지 검사했는데, 비캡처 그룹 "(?:" 이
    쓰이거나 (?i) 위치가 바뀌면 곧바로 오판하는 취약한 방식이었습니다.
    """
    masked_text = text
    for pattern in SENSITIVE_PATTERNS:
        regex = re.compile(pattern, re.DOTALL)

        if regex.groups >= 2:

            def replace_match(match: re.Match[str]) -> str:
                secret_val = match.group(2)
                if not secret_val:
                    return REDACTION
                return match.group(0).replace(secret_val, REDACTION)

            masked_text = regex.sub(replace_match, masked_text)
        else:
            masked_text = regex.sub(REDACTION, masked_text)

    return masked_text


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: privacy_filter.py <텍스트_문자열|->")
        print("  인자가 '-' 이면 표준입력에서 전문을 읽습니다. 민감한 원문을")
        print("  프로세스 목록과 셸 히스토리에 남기지 않으려면 이 방식을 쓰십시오.")
        return 1

    input_text = sys.argv[1]
    if input_text == "-":
        input_text = sys.stdin.read()

    result = scan_and_mask(input_text)

    output = {
        "original_length": len(input_text),
        "masked_text": result,
        "is_modified": input_text != result,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
