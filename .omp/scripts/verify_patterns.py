"""
.omp/workspace.yml 의 security.privacyScanner.patterns 와
.omp/scripts/privacy_filter.py 의 SENSITIVE_PATTERNS 가 동일한지 검사합니다.

두 목록은 서로 다른 소비자를 갖습니다. 앞쪽은 omp 런타임이 직접 읽고, 뒤쪽은
just scan-privacy 가 실행합니다. 그래서 정의를 한 파일로 합칠 수 없고, 사본이
어긋나지 않는지 기계적으로 확인하는 편이 안전합니다.

YAML 파서를 쓰지 않는 이유: 루트 pyproject.toml 에 yaml 의존성이 없고,
workspace.yml 이 `uv add` 를 승인 필요 명령으로 지정하고 있어 이 검사만을 위해
의존성을 늘리지 않습니다. 대신 patterns 블록을 단일 인용 한 줄 스칼라로만
쓰도록 제한하고, 그 형태만 해석합니다. 지원하지 않는 형태를 만나면 조용히
넘기지 않고 오류로 알립니다.
"""

import importlib.util
import os
import sys
from typing import List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE_YML = os.path.join(REPO_ROOT, ".omp", "workspace.yml")
FILTER_SCRIPT = os.path.join(REPO_ROOT, ".omp", "scripts", "privacy_filter.py")


def unquote_yaml_scalar(scalar: str) -> str:
    """
    단일 인용 YAML 스칼라를 원문 문자열로 되돌립니다. 단일 인용 스칼라는
    이스케이프를 해석하지 않으므로 정규식을 담기에 적합하며, 내부의 작은따옴표만
    두 번 반복해 표기합니다.
    """
    s = scalar.strip()
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        raise ValueError(
            f"이중 인용 스칼라는 지원하지 않습니다. 단일 인용으로 바꾸십시오: {s}"
        )
    if s.startswith("|") or s.startswith(">"):
        raise ValueError(
            f"블록 스칼라는 지원하지 않습니다. 단일 인용 한 줄로 바꾸십시오: {s}"
        )
    return s


def extract_yaml_patterns(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    start = None
    base_indent = 0
    for idx, line in enumerate(lines):
        if line.strip() == "patterns:":
            start = idx + 1
            base_indent = len(line) - len(line.lstrip())
            break

    if start is None:
        raise ValueError(f"{path} 에서 'patterns:' 키를 찾지 못했습니다.")

    collected: List[str] = []
    for line in lines[start:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            break
        collected.append(unquote_yaml_scalar(stripped[2:]))

    return collected


def load_script_patterns(path: str) -> List[str]:
    spec = importlib.util.spec_from_file_location("privacy_filter", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} 를 모듈로 적재할 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.SENSITIVE_PATTERNS)


def main() -> int:
    try:
        yaml_patterns = extract_yaml_patterns(WORKSPACE_YML)
        script_patterns = load_script_patterns(FILTER_SCRIPT)
    except Exception as e:
        print(f"패턴 검사 실패: {e}")
        return 1

    if not yaml_patterns:
        print("workspace.yml 의 patterns 목록이 비어 있습니다.")
        return 1

    if yaml_patterns == script_patterns:
        print(f"패턴 정의 일치 · {len(script_patterns)}개 항목이 순서까지 동일합니다.")
        for pattern in script_patterns:
            print(f"  - {pattern}")
        return 0

    print("패턴 정의 불일치를 발견했습니다.")

    only_in_script = [p for p in script_patterns if p not in yaml_patterns]
    only_in_yaml = [p for p in yaml_patterns if p not in script_patterns]

    if only_in_script:
        print("\nprivacy_filter.py 에만 있음 (workspace.yml 이 탐지하지 못함):")
        for pattern in only_in_script:
            print(f"  - {pattern}")

    if only_in_yaml:
        print("\nworkspace.yml 에만 있음 (privacy_filter.py 가 마스킹하지 못함):")
        for pattern in only_in_yaml:
            print(f"  - {pattern}")

    if not only_in_script and not only_in_yaml:
        print("\n구성 항목은 같지만 순서가 다릅니다.")
        print(f"  workspace.yml : {yaml_patterns}")
        print(f"  privacy_filter: {script_patterns}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
