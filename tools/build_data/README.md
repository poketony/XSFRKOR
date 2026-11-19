# 에셋 빌드 데이터

`codex-lab`에서 선별한 실사용 입력입니다. 빌드 도중 다시 생성하거나 다른 저장소에서 읽어 오지 않습니다.

| 파일 | 용도 |
| --- | --- |
| `required-originals.json` | 로컬에서 준비할 게임 원본의 경로·크기·SHA-256 |
| `disc-inventory.json`, `contents-report.json` | 지원 디스크의 파일 위치, 압축 멤버·원본 해시 |
| `XENOSAGA KOR-JPN.json` | KS 한글 2,350자의 고정 대응표 |
| `exp/` | 체험판 그래픽 좌표·기준 해시, Pretendard SemiBold 한글 픽셀 |
| `mini/` | 제노피탄 단어, 퍼즐 판, 자동 시연, 진단 이름 슬라이스 및 표제어 확인 자료 |

제노피탄 단어와 설명은 `mini/CampaignWords.tsv`·`mini/XenosagaWords.txt`, 1인용·대전 판 구성은 `mini/StagesKorean.toml`·`mini/VersusKorean.tsv`에서 편집합니다. `CampaignWordIds.tsv`는 세이브에 연결되는 기존 ID이므로 임의로 다시 번호를 매기지 않습니다. `codex-lab`의 같은 이름 파일은 이 빌드에서 읽지 않습니다.

## 표제어 자료 출처

`mini/lexicon.tsv.gz`는 원천 목록에서 현재 퍼즐 정책을 통과하는 행의 출처 ID, 뜻 ID, 검색 키, 표시 표기만 남긴 데이터입니다. 사전 전체 뜻풀이·용례·미디어는 복제하지 않습니다.

- 국립국어원 **한국어기초사전**, 2026-10-19 XML 배포본. [원천](https://krdict.korean.go.kr/dicBatchDownload?seq=219) · [저작권 정책](https://krdict.korean.go.kr/kor/kboardPolicy/copyRightTermsInfo).
- 국립국어원 **표준국어대사전**, 2026-06-05 XML. [사용한 공개 사본](https://github.com/spellcheck-ko/korean-dict-nikl/tree/c31ae259de4cd0a355cf8a19b16e75578fd396e2/stdict) · [저작권 정책](https://stdict.korean.go.kr/join/copyrightPolicy.do).
- 위 두 사전의 파생 자료에는 [CC BY-SA 2.0 KR](https://creativecommons.org/licenses/by-sa/2.0/kr/) 조건을 유지합니다. 별도의 DBC 표제어 참조 및 게임 편집 데이터에 이 조건을 일괄 적용한다는 뜻은 아닙니다.
