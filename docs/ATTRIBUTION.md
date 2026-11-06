# Image attribution

Every photograph in `frontend/public/img/` is a real photograph of cricket, a cricket ground
or a phone in a fan's hand, published under a licence that permits reuse. Nothing here is
stock imagery from an agency, nothing is AI-generated, and no image is captioned as a
specific real match: the fixture in this build is fictional, so captions say what the photo
shows and never imply it shows the fixture.

The same data is served to the UI at `/api/facts` (`photo_credits`) and rendered on the home
page, so the credit travels with the picture instead of living only in this file.

| file | original title | author | licence | source |
|---|---|---|---|---|
| `/img/hero-stadium.jpg` | Wankhede-1.JPG | Sports Fan | Public domain | [https://commons.wikimedia.org/wiki/File:Wankhede-1.JPG](https://commons.wikimedia.org/wiki/File:Wankhede-1.JPG) |
| `/img/stadium-crowd.jpg` | MChinnaswamy-Stadium.jpg | Amarnath.de (talk). | Public domain | [https://commons.wikimedia.org/wiki/File:MChinnaswamy-Stadium.jpg](https://commons.wikimedia.org/wiki/File:MChinnaswamy-Stadium.jpg) |
| `/img/match-wide.jpg` | Pune Warriors India vs deccan chargers AT CUTTACK.jpg | Kamalakanta777 | CC BY-SA 3.0 | [https://commons.wikimedia.org/wiki/File:Pune_Warriors_India_vs_deccan_chargers_AT_CUTTACK.jpg](https://commons.wikimedia.org/wiki/File:Pune_Warriors_India_vs_deccan_chargers_AT_CUTTACK.jpg) |
| `/img/batsman-drive.jpg` | 131106-N-TX154-133 (10822728965).jpg | Naval Surface Warriors | CC BY-SA 2.0 | [https://commons.wikimedia.org/wiki/File:131106-N-TX154-133_(10822728965).jpg](https://commons.wikimedia.org/wiki/File:131106-N-TX154-133_(10822728965).jpg) |
| `/img/batsman-running.jpg` | 131106-N-TX154-405 (10822865154).jpg | Naval Surface Warriors | CC BY-SA 2.0 | [https://commons.wikimedia.org/wiki/File:131106-N-TX154-405_(10822865154).jpg](https://commons.wikimedia.org/wiki/File:131106-N-TX154-405_(10822865154).jpg) |
| `/img/wicket-bails.jpg` | Cricket bails.jpg | Aravind Sivaraj | CC BY-SA 3.0 | [https://commons.wikimedia.org/wiki/File:Cricket_bails.jpg](https://commons.wikimedia.org/wiki/File:Cricket_bails.jpg) |
| `/img/crowd-celebration.jpg` | Whitgift School - CB40 - Surrey v Hants - May 2011 - "The Crowd are on the Pitch, They Think It's All Over" (5755954247).jpg | Gareth Williams from Redhill, England | CC BY 2.0 | [https://commons.wikimedia.org/wiki/File:Whitgift_School_-_CB40_-_Surrey_v_Hants_-_May_2011_-_%22The_Crowd_are_on_the_Pitch,_They_Think_It%27s_All_Over%22_(5755954247).jpg](https://commons.wikimedia.org/wiki/File:Whitgift_School_-_CB40_-_Surrey_v_Hants_-_May_2011_-_%22The_Crowd_are_on_the_Pitch,_They_Think_It%27s_All_Over%22_(5755954247).jpg) |
| `/img/phone-in-hand.jpg` | Smartphone in Hand (Unsplash).jpg | NeONBRAND neonbrand | CC0 | [https://commons.wikimedia.org/wiki/File:Smartphone_in_Hand_(Unsplash).jpg](https://commons.wikimedia.org/wiki/File:Smartphone_in_Hand_(Unsplash).jpg) |
| `/img/scoreboard.jpg` | Headingley Cricket Ground - the scoreboard - geograph.org.uk - 1996034.jpg | John Sutton | CC BY-SA 2.0 | [https://commons.wikimedia.org/wiki/File:Headingley_Cricket_Ground_-_the_scoreboard_-_geograph.org.uk_-_1996034.jpg](https://commons.wikimedia.org/wiki/File:Headingley_Cricket_Ground_-_the_scoreboard_-_geograph.org.uk_-_1996034.jpg) |
| `/img/ground-pavilion.jpg` | Cricket at The Doon School.jpg | KramerVsSchramer | CC BY-SA 4.0 | [https://commons.wikimedia.org/wiki/File:Cricket_at_The_Doon_School.jpg](https://commons.wikimedia.org/wiki/File:Cricket_at_The_Doon_School.jpg) |
| `/img/stadium-day.jpg` | Chinnaswamystadium.jpg | The original uploader was Latecut at English Wikipedia. | Public domain | [https://commons.wikimedia.org/wiki/File:Chinnaswamystadium.jpg](https://commons.wikimedia.org/wiki/File:Chinnaswamystadium.jpg) |

## Licence notes

* **Public domain** images may be reused without condition; attribution is given anyway.
* **CC BY-SA 2.0 / 3.0 / 4.0** images require attribution and share-alike for derivative
  images. They are used unmodified apart from crop and resize, and each credit above names
  the author, the licence and the source file.
* **CC0** requires nothing; attribution is given anyway.

## Generated content in this repository

* The scoreboard frames under `backend/fixtures/frames/` are **rendered** from the fixture
  feed by `backend/fixtures/build_fixtures.py` (Pillow + DejaVu Sans Bold), not photographed.
  The UI labels them `rendered_fixture` wherever they appear.
* The audio under `backend/fixtures/audio/` is synthetic speech used as ASR input; it is not a
  recording of a real broadcaster.
* The match itself (teams, players, scores) is fictional and marked `fictional: true` in
  `/api/facts`.
