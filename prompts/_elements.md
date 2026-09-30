## 레이아웃 JSON 규격
좌표계: 슬라이드 좌상단 (0,0) ~ 우하단 (100,100), 퍼센트. 슬라이드 비율 {{SLIDE_RATIO}} (폭 {{SLIDE_W}}pt × 높이 {{SLIDE_H}}pt).
모든 요소에 고유 "id" (e1, e2 …). 뒤에 나오는 요소가 위에 그려진다.

- {"id","type":"text","x","y","w","h","text","size"(pt),"bold","color","align":"left|center|right","valign":"top|middle|bottom","line_spacing"}
  한 문단 안에서 색/굵기를 섞을 땐 "text" 대신 "runs":[{"text","color","bold","size"}]. 줄바꿈은 "\n".
- {"id","type":"rect","x","y","w","h","fill"(HEX|null),"line"(HEX|null),"line_w"(pt),"radius"(0~0.5),"text","size","color"}
- {"id","type":"ellipse", rect와 동일}
- {"id","type":"line","x1","y1","x2","y2","color","width"(pt),"dash"(bool),"arrow"(bool: 끝점에 화살촉)}
- {"id","type":"donut","x","y","w","h","values":[..],"labels":[..],"colors":[..],"hole":0.5~0.8}
- {"id","type":"bar","x","y","w","h","categories":[..],"values":[..],"colors":[..],"horizontal"(bool),"number_format":"0.0\"배\""}
- {"id","type":"image","x","y","w","h","source","label","fit":"contain|stretch"}
  source:
    "user:<파일명>"  → 사용자가 올린 실제 자산. 사용 가능: {{USER_ASSETS}}
    "crop_source" + "src":{"x","y","w","h"} → 원본 장표 이미지에서 해당 영역(퍼센트)을 잘라 사용. 게임 화면/스크린샷/굿즈 사진은 이것으로.
    "gen:<영문-kebab-이름>" + "prompt"(영어, 단독 생성용, 텍스트 없음) + "kind":"icon|background|object" → 새로 생성할 에셋.
       아이콘/배경/추상 오브젝트만 허용. 게임 화면·캐릭터·로고·인물·UI 생성 금지. 꼭 필요한 곳에만 (장표당 최대 {{MAX_ASSETS}}개).
    "file:<경로>" → 이미 있는 이미지 (현재 레이아웃에 있으면 그대로 유지)

## 배치 원칙
- 좌우 여백 최소 6%, 상단 여백 최소 7%. 요소끼리 겹치지 않게 (의도한 겹침 제외: 카드 위 텍스트 등).
- 텍스트 상자 높이는 (줄 수 × 글자크기 × 1.3) 이상. 한글 1자 폭 ≈ 글자크기.
- 차트는 반드시 donut/bar 요소로 (이미지로 만들지 말 것). 원본에 없는 값으로 차트 만들지 말 것.
- 색상은 디자인 가이드 팔레트만. 폰트 크기는 가이드 범위.
