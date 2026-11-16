# 그래픽 편집

**PNG를 수정한 뒤 옆에 `원래이름_KOR.png`로 저장하고, 저장소 루트 `Rebuild.bat`를 실행하면 됩니다.** 원본 PNG와 가로·세로 크기를 유지하세요. 색상 수와 알파는 해당 게임 형식에 맞춰 자동 변환합니다.

메뉴·강조 대사 62개, 추가 ADV·DTB 그림 24개, `MINI_SPR_번호.png` 104개, **`EXP` 폴더의 체험판 그림 60개**, 공용 저장 아이콘 부제 1개로 합계 251개입니다. MINI 원본 텍스처 105개 중 `159`·`160`은 편집 PNG 한 장으로 합쳤습니다. 일본어가 확인된 대상과 승인된 오프닝 각색 그림만 선별했으며 영어 전용 그림이나 배경을 전부 추출하지 않았습니다. MINI의 퍼즐 글자 시트는 현재 일반 그래픽 편집 대상이 아닙니다.

2026-11-04 누락 재검토로 `MINI_SPR_017.png`(오프닝 키스 뜻풀이)와 `MINI_SPR_140.png`(결과·진단 문구)를 추가했습니다. 이 두 장도 옆에 `_KOR.png`를 두고 같은 `Rebuild.bat`로 리빌드합니다. [누락 원인과 검증](../codex-lab/freaks/MINI-GRAPHICS.md#누락-재검토)을 참고하세요.

오프닝의 가지 대체 그림은 `MINI_SPR_015_KOR.png`입니다. 승인된 ‘솔로들의 영웅’ 그림을 배치했고 같은 BAT가 읽습니다. 958종 확장 테스트 파일은 루트 일반 빌드와 구분해 [시험 빌드 안내](../codex-lab/freaks/alphabet-expansion/PLAYTEST-958.md)를 따르세요.

**저장 아이콘은 `SAVE_ICON_SUBTITLE.png`를 수정해 `SAVE_ICON_SUBTITLE_KOR.png`로 저장합니다.** 불투명 RGB/RGBA, 43×9 크기를 유지하세요. 같은 BAT가 런처·ADV의 아이콘 두 사본을 함께 만듭니다. 모델·애니메이션과 나머지 그림은 보존합니다. [저장 에셋 사용법](../codex-lab/freaks/SAVE-ASSETS.md)을 참고하세요.

체험판의 메뉴·맵 이름·조작 안내·타이틀은 **`graphics/EXP`의 PNG 옆에 `_KOR.png`를 두고 같은 루트 BAT를 실행합니다.** [EXP 사용법](EXP/README.md)에 회색조 마스크와 타이틀 알파, JPEG의 별도 제한을 적었습니다.

결과는 `rebuilt`에 나옵니다. 저장소 최상단 `build.json`의 `outputs`에 기록된 GRP·ELF·폰트를 모두 외부 `XenosagaFreaks/UNPACKED`의 같은 경로에 반영하세요. ELF와 아카이브가 서로 맞아야 하므로 이전 빌드 결과를 섞지 마세요.

**`MINI_SPR_018`·`285`·`290~293`·`296~297`은 읽기 쉽게 조립한 PNG입니다.** 같은 이름에 `_KOR`만 붙여 수정본을 두면 BAT가 원래 조각에 자동으로 되돌립니다. 새 PNG의 크기·투명도를 유지하세요. `018`은 320×156이며, 제목 아래의 안내문과 장식은 각각 독립된 조각입니다. 예전 원본 시트 크기의 수정본도 계속 지원합니다. 두 팔레트는 원본대로 유지하므로 색상별로 다시 편집할 필요는 없습니다. [캔버스 크기와 제한](../codex-lab/freaks/MINI-GRAPHICS.md#조립형-배너-편집)을 참고하세요.

**튜토리얼은 `MINI_SPR_159.png`의 완성 화면(640×448)을 수정해 `MINI_SPR_159_KOR.png`로 저장합니다.** 별도 `160.png`는 필요 없으며 BAT가 두 원본 텍스처에 자동 분배합니다. 예전 `159_KOR`·`160_KOR`도 지원하지만 같은 픽셀의 수정 내용이 충돌하면 중단합니다. [통합 화면 사용법](../codex-lab/freaks/MINI-GRAPHICS.md#튜토리얼-전체-화면)을 참고하세요.

**`MINI_SPR_054.png`는 말풍선별 완성 문장을 조립한 364×336 편집본입니다.** 기존 글자 영역을 수정해 `MINI_SPR_054_KOR.png`로 저장하고 같은 BAT를 실행하세요. 말풍선 테두리·배경은 미리보기이므로 유지합니다. 기존 256×256 시트의 `_KOR.png`도 계속 지원합니다. [배치와 편집 범위](../codex-lab/freaks/MINI-GRAPHICS.md#054-말풍선-문구)

**엔딩의 `MINI_SPR_192_KOR.png`·`193_KOR.png`는 한 쌍입니다.** 각각 ‘올 클리어!!’·‘축하합니다!!’이며, `192`의 ‘다’ 타일도 `193` 문구에서 사용합니다. 타일 자리를 유지하고 같은 BAT를 실행하면 글자 선택표와 가운데 위치도 함께 반영됩니다. 결과 `MINI.ELF`·`WWROM.DAT`를 함께 적용하세요. [글꼴·타일 구성·검증](../codex-lab/freaks/MINI-GRAPHICS.md#엔딩-축하-문구)

세부 대상은 [기존 메뉴](../codex-lab/freaks/GRAPHICS-PIPELINE.md)와 [MINI 그림](../codex-lab/freaks/MINI-GRAPHICS.md)에 정리했습니다.

추가된 24개도 같은 BAT로 리빌드합니다.

| PNG 이름 | 수정할 내용 |
| --- | --- |
| `ADV_ADSBG_0100.png`~`ADV_ADSBG_0119.png` | 그림에 박힌 데이터베이스 제목·설명문. 일반·어두운 표시를 각각 수정 |
| `DTB_SNAP_0025.png`, `DTB_SNAP_0027.png`, `DTB_SNAP_0029.png` | 설정화의 일본어 제목·주석 |
| `DTB_THUMB_0082.png` | 장면 하단에 박힌 일본어 자막 |

예: `DTB_SNAP_0025_KOR.png`를 옆에 두고 루트 `Rebuild.bat`를 실행합니다. [선별·검증 기록](../codex-lab/freaks/GRAPHICS-AUDIT.md)에 원본 파일과 참조 구조를 정리했습니다.
