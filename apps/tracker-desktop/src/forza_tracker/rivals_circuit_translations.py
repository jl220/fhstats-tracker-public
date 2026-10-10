"""French and Russian Rivals circuit names supplied by the community.

Source: FHStats_Circuit_Translations.xlsx, Circuits!A6:G91 (2026-10-06).
Keys retain existing catalog slugs, including historical English spellings.
Kept separate from snapshots so existing user captures remain untouched.
"""


RIVALS_CIRCUIT_TRANSLATIONS: dict[str, dict[str, str]] = {
    'road:highway-circuit': {
        "name_fr": "Circuit d'autoroute",
        "name_ru": 'Гонка на автомагистрали',
    },
    'road:narai-juku-circuit': {
        "name_fr": 'Circuit de Narai-Juku',
        "name_ru": 'Трасса Нарай-дзюку',
    },
    'road:shirakawa-circuit': {
        "name_fr": 'Circuit de Shirakawa',
        "name_ru": 'Трасса Сиракава',
    },
    'road:shimanoyama-circuit': {
        "name_fr": 'Circuit de Shimanoyama',
        "name_ru": 'Трасса Симанояма',
    },
    'road:hokubu-circuit': {
        "name_fr": "Circuit d'Hokubu",
        "name_ru": 'Трасса Хокубу',
    },
    'road:soni-circuit': {
        "name_fr": 'Circuit de Soni',
        "name_ru": 'Трасса Сони',
    },
    'road:daikoku-circuit': {
        "name_fr": 'Circuit de Daikoku',
        "name_ru": 'Трасса Дайкоку',
    },
    'road:electric-town-circuit': {
        "name_fr": 'Circuit de la ville aux néons',
        "name_ru": 'Трасса электронного города',
    },
    'road:legendary-island-circuit': {
        "name_fr": 'Circuit de Legend Island',
        "name_ru": 'Трасса на Острове легенд',
    },
    'road:irokawa-circuit': {
        "name_fr": "Circuit d'Irokawa",
        "name_ru": 'Трасса Ирокава',
    },
    'road:edamame-circuit': {
        "name_fr": 'Circuit edamame',
        "name_ru": 'Трасса "Эдамаме"',
    },
    'road:festival-sprint': {
        "name_fr": 'Sprint du festival',
        "name_ru": 'Фестивальный спринт',
    },
    'road:venus-sprint': {
        "name_fr": 'Sprint de Vénus',
        "name_ru": 'Спринт "Венера"',
    },
    'road:seaside-park-sprint': {
        "name_fr": 'Sprint du parc côtier',
        "name_ru": 'Спринт по приморскому парку',
    },
    'road:ito-sprint': {
        "name_fr": "Sprint d'Ito",
        "name_ru": 'Спринт Ито',
    },
    'road:tateyama-kurobe-sprint': {
        "name_fr": 'Sprint de Tateyama Kurobe',
        "name_ru": 'Спринт по Татэям-Куробэ',
    },
    'road:shimanoyama-sprint': {
        "name_fr": 'Sprint de Shimanoyama',
        "name_ru": 'Спринт по Симанояме',
    },
    'road:satta-sprint': {
        "name_fr": 'Sprint de Satta',
        "name_ru": 'Спринт по перевалу Сатта',
    },
    'road:coastline-sprint': {
        "name_fr": 'Sprint sur la côte',
        "name_ru": 'Спринт по побережью',
    },
    'road:tokyo-train-sprint': {
        "name_fr": 'Sprint de la voie ferrée de Tokyo',
        "name_ru": 'Спринт по железной дороге в Токио',
    },
    'road:shikisai-sprint': {
        "name_fr": 'Sprint de Shikisai',
        "name_ru": 'Спринт по Сикисай',
    },
    'road:colossus': {
        "name_fr": 'Le Colosse',
        "name_ru": 'Колосс',
    },
    'road:goliath': {
        "name_fr": 'Le Goliath',
        "name_ru": 'Голиаф',
    },
    'street:daikoku-chase': {
        "name_fr": 'Poursuite de Daikoku',
        "name_ru": 'Погоня в Дайкоку',
    },
    'street:norikura-descent': {
        "name_fr": 'Descente de Norikura',
        "name_ru": 'Спуск Норикура',
    },
    'street:nachi-run': {
        "name_fr": 'Course de Nachi',
        "name_ru": 'Заезд Нати',
    },
    'street:hokubu-ascent': {
        "name_fr": "Ascension d'Hokubu",
        "name_ru": 'Подъем Хокубу',
    },
    'street:okishinaimura-run': {
        "name_fr": "Course d'Okishinaimura",
        "name_ru": 'Заезд Окисинаймура',
    },
    'street:sunflower-charge': {
        "name_fr": 'Charge des tournesols',
        "name_ru": 'Гонка среди подсолнухов',
    },
    'street:kita-ine': {
        "name_fr": 'Kita Ine',
        "name_ru": 'Кита Инэ',
    },
    'street:rainbow-bridge-descent': {
        "name_fr": 'Descente du pont arc-en-ciel',
        "name_ru": 'Спуск по радужному мосту',
    },
    'street:shimanoyama-charge': {
        "name_fr": 'Charge de Shimanoyama',
        "name_ru": 'Гонка Симанояма',
    },
    'street:festival-chase': {
        "name_fr": 'Poursuite du festival',
        "name_ru": 'Погоня на фестивале',
    },
    'street:matsumi-climb': {
        "name_fr": 'Ascension de Matsumi',
        "name_ru": 'Подъем на Мацуме',
    },
    'street:minami-chase': {
        "name_fr": 'Poursuite Minami',
        "name_ru": 'Погоня в Минами',
    },
    'street:river-descent': {
        "name_fr": 'Descente de la rivière',
        "name_ru": 'Спуск у реки',
    },
    'street:cedar-run': {
        "name_fr": 'Course des cèdres',
        "name_ru": 'Кедровый заезд',
    },
    'street:tokyo-city-docks-charge': {
        "name_fr": 'Charge des quais de Tokyo',
        "name_ru": 'Гонка в токийских доках',
    },
    'dirt:chiheisen-scramble': {
        "name_fr": 'Epreuve mixte de Chiheisen',
        "name_ru": 'Кольцевой кросс Тихэйсэн',
    },
    'dirt:sotoyama-scramble': {
        "name_fr": 'Epreuve mixte de Sotoyama',
        "name_ru": 'Кольцевой кросс Сотояма',
    },
    'dirt:hirosaki-scramble': {
        "name_fr": 'Epreuve mixte de Hirosaki',
        "name_ru": 'Кольцевой кросс Хиросаки',
    },
    'dirt:sunflower-scramble': {
        "name_fr": 'Epreuve mixte des tournesols',
        "name_ru": 'Кольцевой кросс по полям подсолнуха',
    },
    'dirt:bamboo-forest-scramble': {
        "name_fr": 'Epreuve mixte de la forêt de bambous',
        "name_ru": 'Кольцевой кросс через Бамбуковый лес',
    },
    'dirt:ine-scramble': {
        "name_fr": "Epreuve mixte d'Ine",
        "name_ru": 'Кольцевой кросс Инэ',
    },
    'dirt:horizon-stadium-scramble': {
        "name_fr": 'Epreuve mixte du stade Horizon',
        "name_ru": 'Кольцевой кросс на стадионе Horizon',
    },
    'dirt:tayaki-scramble': {
        "name_fr": 'Epreuve mixte des taiyakis',
        "name_ru": 'Кольцевой кросс "Тайяки"',
    },
    'dirt:kawazu-nanadaru-scramble': {
        "name_fr": 'Epreuve mixte de Kawazu Nanadaru',
        "name_ru": 'Кольцевой кросс Кавадзу Нанадару',
    },
    'dirt:sekibe-scramble': {
        "name_fr": 'Epreuve mixte de Sekibe',
        "name_ru": 'Кольцевой кросс Сэкибэ',
    },
    'dirt:nukabira-trail': {
        "name_fr": 'Trail de Nukabira',
        "name_ru": 'Спринт-кросс Никабира',
    },
    'dirt:waterfall-trail': {
        "name_fr": 'Trail de la cascade',
        "name_ru": 'Спринт-кросс у водопада',
    },
    'dirt:hokubu-trail': {
        "name_fr": "Trail d'Hokubu",
        "name_ru": 'Спринт-кросс Хокубу',
    },
    'dirt:ito-trail': {
        "name_fr": "Trail d'Ito",
        "name_ru": 'Спринт-кросс Ито',
    },
    'dirt:airfield-trail': {
        "name_fr": "Trail de l'aérodrome",
        "name_ru": 'Спринт-кросс по аэродрому',
    },
    'dirt:cherry-fields-trail': {
        "name_fr": 'Trail du champ de cerisiers',
        "name_ru": 'Спринт-кросс по вишневому полю',
    },
    'dirt:kinkaku-ji-trail': {
        "name_fr": 'Trail de Kinkaku-ji',
        "name_ru": 'Спринт-кросс Кинкаку-дзи',
    },
    'dirt:legend-island-trail': {
        "name_fr": 'Trail de Legend Island',
        "name_ru": 'Спринт-кросс по острову легенд',
    },
    'dirt:oyashirazu-trail': {
        "name_fr": "Trail d'Oyashirazu",
        "name_ru": 'Спринт-кросс Оясирадзу',
    },
    'dirt:takashiro-trail': {
        "name_fr": 'Trail de Takashiro',
        "name_ru": 'Спринт-кросс Такасиро',
    },
    'dirt:gauntlet': {
        "name_fr": 'Le Gantelet',
        "name_ru": 'Марш-бросок',
    },
    'cross-country:legend-island-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country de Legend Island',
        "name_ru": 'Кольцевой автокросс по Острову легенд',
    },
    'cross-country:naruo-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country de Naruo',
        "name_ru": 'Кольцевой автокросс Наруо',
    },
    'cross-country:snow-forest-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country de la forêt enneigée',
        "name_ru": 'Кольцевой автокросс в Снежном лесу',
    },
    'cross-country:oka-cross-country-circuit': {
        "name_fr": "Circuit de cross-country d'Oka",
        "name_ru": 'Кольцевой автокросс Ока',
    },
    'cross-country:edogawa-cross-country-circuit': {
        "name_fr": "Circuit de cross-country d'Edogawa",
        "name_ru": 'Кольцевой автокросс Эдогава',
    },
    'cross-country:city-docks-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country des quais',
        "name_ru": 'Кольцевой автокросс в городских доках',
    },
    'cross-country:stadium-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country du stade',
        "name_ru": 'Кольцевой автокросс на стадионе',
    },
    'cross-country:nangan-cross-country-circuit': {
        "name_fr": 'Circuit de cross-country de Nangan',
        "name_ru": 'Кольцевой автокросс Наньган',
    },
    'cross-country:soni-highlands-cross-country': {
        "name_fr": 'Cross-country du plateau de Soni',
        "name_ru": 'Автокросс по нагорью Сони',
    },
    'cross-country:ruriko-ji-cross-country': {
        "name_fr": 'Cross-country de Ruriko-ji',
        "name_ru": 'Автокросс Рурико-дзи',
    },
    'cross-country:takashiro-cross-country': {
        "name_fr": 'Cross-country de Takashiro',
        "name_ru": 'Автокросс Такасиро',
    },
    'cross-country:tateyama-alpine-cross-country': {
        "name_fr": 'Cross-country alpin de Tateyama',
        "name_ru": 'Альпийский автокросс Татэяма',
    },
    'cross-country:wind-farm-cross-country': {
        "name_fr": 'Cross-country du parc éolien',
        "name_ru": 'Автокросс на электростанции',
    },
    'cross-country:yahikoyama-cross-country': {
        "name_fr": 'Cross-country du mont Yahiko',
        "name_ru": 'Автокросс Яхикояма',
    },
    'cross-country:shimanoyama-cross-country': {
        "name_fr": 'Cross-country de Shimanoyama',
        "name_ru": 'Автокросс Симанояма',
    },
    'cross-country:shinjuku-gyoen-cross-country': {
        "name_fr": 'Cross-country de Shinjuku Gyoen',
        "name_ru": 'Автокросс Синдзюку-гёэн',
    },
    'cross-country:temple-cross-country': {
        "name_fr": 'Cross-country du temple',
        "name_ru": 'Автокросс у храма',
    },
    'cross-country:izu-cross-country': {
        "name_fr": "Cross-country d'Izu",
        "name_ru": 'Автокросс Идзу',
    },
    'cross-country:the-titan': {
        "name_fr": 'Le Titan',
        "name_ru": 'Титан',
    },
    'drag:horizon-festival-drag-strip': {
        "name_fr": 'Piste de drag du festival Horizon',
        "name_ru": 'Дрэг-стрип на фестивале Horizon',
    },
    'drag:irokawa-space-center-drag-strip': {
        "name_fr": "Piste de drag du centre spacial d'Irokawa",
        "name_ru": 'Дрэг-стрип в космическом центре',
    },
    'drag:ito-airfield-drag-strip': {
        "name_fr": "Piste de drag de l'aérodrome d'Ito",
        "name_ru": 'Дрэг-стрип на аэродроме',
    },
    'touge:hakone-nanamagari': {
        "name_fr": 'Hakone Nanamagari',
        "name_ru": 'Хакоэ Нанамагари',
    },
    'touge:norikura-skyline': {
        "name_fr": 'Route panoramique de Norikura',
        "name_ru": 'Трасса Норикура',
    },
    'touge:arashiyama-takao': {
        "name_fr": 'Arashiyama Takao',
        "name_ru": 'Арасияма Такао',
    },
    'touge:bandai-azuma': {
        "name_fr": 'Bandai Azuma',
        "name_ru": 'Бандай-Адзума',
    },
    'touge:mt-haruna': {
        "name_fr": 'Mont Haruna',
        "name_ru": 'Гора Харуна',
    },
}
