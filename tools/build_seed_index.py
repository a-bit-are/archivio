#!/usr/bin/env python3
"""
Costruisce gli indici offline (seed) usati dall'autocompletamento istantaneo.

Fonti (dati aperti, compatibili con uso commerciale):
  - ListenBrainz / MusicBrainz (MetaBrainz Foundation) – dati CC0
  - Open Library (Internet Archive) – metadati bibliografici aperti

Uso (da eseguire solo in fase di sviluppo, richiede rete):
    python3 tools/build_seed_index.py            # musica + libri
    python3 tools/build_seed_index.py music      # solo musica
    python3 tools/build_seed_index.py books      # solo libri

Output:
    app/archivio/data/seed_music.json
    app/archivio/data/seed_books.json
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "app", "archivio", "data")
UA = "Archivio/1.0.0 (OpenSource; https://github.com/a-bit-are/archivio)"

ITALIAN_ARTISTS = [
    "Vasco Rossi", "Fabrizio De André", "Lucio Battisti", "Lucio Dalla", "Francesco De Gregori",
    "Franco Battiato", "Mina", "Adriano Celentano", "Zucchero", "Pino Daniele", "Ligabue",
    "Eros Ramazzotti", "Laura Pausini", "Gianna Nannini", "Renato Zero", "Claudio Baglioni",
    "Antonello Venditti", "Fiorella Mannoia", "Francesco Guccini", "Paolo Conte", "Rino Gaetano",
    "Ivano Fossati", "Edoardo Bennato", "Eugenio Finardi", "Loredana Bertè", "Mia Martini",
    "Ornella Vanoni", "Patty Pravo", "Gino Paoli", "Luigi Tenco", "Domenico Modugno",
    "Fabio Concato", "Riccardo Cocciante", "Umberto Tozzi", "Raf", "Biagio Antonacci",
    "Tiziano Ferro", "Jovanotti", "Max Pezzali", "883", "Negramaro", "Subsonica", "Afterhours",
    "Marlene Kuntz", "Verdena", "Litfiba", "Pooh", "Nomadi", "Premiata Forneria Marconi",
    "Banco del Mutuo Soccorso", "Le Orme", "New Trolls", "Area", "Goblin", "Matia Bazar",
    "Elio e le Storie Tese", "Articolo 31", "Caparezza", "Daniele Silvestri", "Niccolò Fabi",
    "Max Gazzè", "Carmen Consoli", "Elisa", "Giorgia", "Alex Britti", "Samuele Bersani",
    "Vinicio Capossela", "Paolo Nutini", "Cesare Cremonini", "Lùnapop", "Modà", "Måneskin",
    "Ultimo", "Mahmood", "Marco Mengoni", "Fedez", "Sfera Ebbasta", "Calcutta", "Thegiornalisti",
    "Tommaso Paradiso", "Coez", "Baustelle", "Brunori Sas", "Motta", "Levante", "Annalisa",
    "Emma", "Alessandra Amoroso", "Francesco Gabbani", "Il Volo", "Andrea Bocelli",
    "Luciano Pavarotti", "Ennio Morricone", "Nino Rota", "Ludovico Einaudi", "Giovanni Allevi",
    "Stefano Bollani", "Paolo Fresu", "Enrico Rava", "Nek", "Gigi D'Alessio", "Massimo Ranieri",
    "Renzo Arbore", "Enzo Jannacci", "Giorgio Gaber", "Fred Buscaglione", "Bruno Lauzi",
    "Sergio Endrigo", "Umberto Bindi", "Angelo Branduardi", "Alice", "Raffaella Carrà",
    "Gianni Morandi", "Al Bano", "Ricchi e Poveri", "Toto Cutugno", "Gigliola Cinquetti",
    "Nada", "Anna Oxa", "Ron", "Luca Carboni", "Stadio", "Gianluca Grignani", "Irene Grandi",
    "Paola Turci", "Morgan", "Bluvertigo", "Tiromancino", "Planet Funk", "Gigi D'Agostino",
    "Eiffel 65", "Righeira", "Sabrina Salerno", "Giorgio Moroder", "Lucio Battisti & Mogol",
    "Edoardo Vianello", "Rita Pavone", "Little Tony", "I Camaleonti", "Equipe 84", "I Giganti",
    "Dik Dik", "Formula 3", "Il Banco", "Osanna", "Napoli Centrale", "James Senese",
    "Tullio De Piscopo", "Enzo Avitabile", "Almamegretta", "99 Posse", "Frankie hi-nrg mc",
    "Neffa", "Sangiovanni", "Blanco", "Lazza", "Geolier", "Salmo", "Marracash", "Gué",
    "Club Dogo", "Fabri Fibra", "J-Ax", "Cristina D'Avena",
]


def http_json(url, data=None, timeout=40, retries=4, headers=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            code = getattr(e, "code", None)
            wait = 6 if code in (429, 503) else 2
            time.sleep(wait * (attempt + 1))
    print("   ! fallita:", url[:110], last, flush=True)
    return None


def year_of(date):
    date = (date or "").strip()
    return date[:4] if len(date) >= 4 and date[:4].isdigit() else ""


# ---------------------------------------------------------------- MUSICA
CURATED_ALBUMS = [
    # [title, artist, year, popularity, mbid]
    ["The Dark Side of the Moon", "Pink Floyd", "1973", 950000, "f5093c06-23e3-404f-aeaa-40f72885ee3a"],
    ["The Wall", "Pink Floyd", "1979", 820000, "f20263c7-57b3-3030-9549-838b53238e53"],
    ["Wish You Were Here", "Pink Floyd", "1975", 790000, "9386b868-f265-3b7f-a226-31f7216228e2"],
    ["Animals", "Pink Floyd", "1977", 650000, "202f1b43-095b-3a14-a90b-4445b51695b1"],
    ["Meddle", "Pink Floyd", "1971", 520000, "9f10b724-f815-315d-903a-730d8ba59d39"],
    ["The Piper at the Gates of Dawn", "Pink Floyd", "1967", 450000, "49cd8f4e-6939-3029-95f8-e51911303f14"],
    ["The Division Bell", "Pink Floyd", "1994", 440000, "5b4b1f84-b610-332b-9f1f-8f4d43a81e7f"],
    ["Abbey Road", "The Beatles", "1969", 940000, "9162580e-5df4-32de-80cc-f45a8d8a9b1d"],
    ["Sgt. Pepper’s Lonely Hearts Club Band", "The Beatles", "1967", 880000, "9f7a4c28-8c28-396d-9200-273f292e5807"],
    ["Revolver", "The Beatles", "1966", 860000, "f0f53c43-7b6c-3f56-9091-1f85a6f924f5"],
    ["The Beatles", "The Beatles", "1968", 840000, "055be730-dcad-31bf-b550-45ba9c202aa3"],
    ["Rubber Soul", "The Beatles", "1965", 810000, "3fadeb86-8225-307a-9554-199220e889b7"],
    ["Let It Be", "The Beatles", "1970", 780000, "1b022e01-4da6-387b-8658-8678046e4cef"],
    ["Led Zeppelin IV", "Led Zeppelin", "1971", 890000, "e3be2324-82f3-3a09-9a22-6e11a6e39321"],
    ["Led Zeppelin", "Led Zeppelin", "1969", 770000, "4ef6d6a9-c90f-3112-b016-f8a48b24f682"],
    ["Led Zeppelin II", "Led Zeppelin", "1969", 760000, "c9f16a46-11c4-3122-a3e8-8b211a8b671f"],
    ["Physical Graffiti", "Led Zeppelin", "1975", 710000, "5be8b2d4-3707-3669-9431-2a32b2005579"],
    ["A Night at the Opera", "Queen", "1975", 850000, "65933730-1c4f-3506-b62c-0c473802f713"],
    ["News of the World", "Queen", "1977", 740000, "84a4590e-652f-32d9-8380-f4875e422126"],
    ["Innuendo", "Queen", "1991", 680000, "81475516-411c-3f62-a813-411f7426466b"],
    ["Rumours", "Fleetwood Mac", "1977", 890000, "202f36a8-c0c9-3029-a080-52340b712a98"],
    ["Thriller", "Michael Jackson", "1982", 920000, "f32fab67-77dd-3937-addc-9062e28e4c37"],
    ["Bad", "Michael Jackson", "1987", 780000, "d2015524-03a5-3c05-9f90-a0a8f10a3b04"],
    ["Back in Black", "AC/DC", "1980", 880000, "d6010be3-98f8-322c-a6c9-787e2e491e58"],
    ["Highway to Hell", "AC/DC", "1979", 810000, "6f627792-0a05-327a-8156-1e567930093b"],
    ["Nevermind", "Nirvana", "1991", 930000, "1b022e01-4da6-387b-8658-8678046e4cef"],
    ["In Utero", "Nirvana", "1993", 790000, "2a0981fb-9593-3019-864b-ce934d97a16e"],
    ["OK Computer", "Radiohead", "1997", 940000, "b1392450-e666-3926-a536-22c65f834433"],
    ["Kid A", "Radiohead", "2000", 850000, "e714c33b-563f-3a57-b31f-0317c3605b98"],
    ["In Rainbows", "Radiohead", "2007", 870000, "6e335887-60ba-38f0-95af-fae7774336bf"],
    ["The Bends", "Radiohead", "1995", 810000, "4979b80d-1960-3d36-a9b4-21f43922c629"],
    ["Brothers in Arms", "Dire Straits", "1985", 830000, "8d51c27a-f538-3f2a-82e9-550596a38219"],
    ["Dire Straits", "Dire Straits", "1978", 740000, "87d1c487-62f8-3078-9e94-7a3173154605"],
    ["Making Movies", "Dire Straits", "1980", 670000, "34574e23-1d32-3120-976e-f5e6e07f780c"],
    ["Kind of Blue", "Miles Davis", "1959", 840000, "8e0390c2-53e2-3774-9604-9850c5024c48"],
    ["Bitches Brew", "Miles Davis", "1970", 620000, "3c302402-6c7a-3f31-85b1-71a41d83804c"],
    ["A Love Supreme", "John Coltrane", "1965", 760000, "99a9868c-2046-3f36-b62c-849a67a54907"],
    ["Blue Train", "John Coltrane", "1958", 680000, "1b9506a2-6306-3063-9131-14796e0744e5"],
    ["Time Out", "The Dave Brubeck Quartet", "1959", 690000, "5a800e26-846f-3c8c-b411-0f1585852588"],
    ["The Rise and Fall of Ziggy Stardust and the Spiders from Mars", "David Bowie", "1972", 850000, "f1ef77c6-c192-36c2-a791-505d90224739"],
    ["Heroes", "David Bowie", "1977", 760000, "c5e4124f-6034-3a57-b31f-0317c3605b98"],
    ["Blackstar", "David Bowie", "2016", 710000, "746f5920-6900-4a8e-9a4e-31f92a357305"],
    ["Master of Puppets", "Metallica", "1986", 860000, "3d00fb45-f8ab-3436-a8e1-b4bfc4d66913"],
    ["Metallica", "Metallica", "1991", 890000, "e8f70201-8899-3f0c-9e07-5d6495bc8046"],
    ["Ride the Lightning", "Metallica", "1984", 820000, "42428027-0723-3996-9614-6b0b50e72916"],
    ["Paranoid", "Black Sabbath", "1970", 840000, "5224c8c9-b538-3f13-9657-1a2961239a00"],
    ["The Joshua Tree", "U2", "1987", 830000, "7e853800-87e1-3e20-a11d-25600a23f011"],
    ["Achtung Baby", "U2", "1991", 760000, "0215c27c-0101-3202-9a21-51a604224b0a"],
    ["Born to Run", "Bruce Springsteen", "1975", 790000, "202f36a8-c0c9-3029-a080-52340b712a99"],
    ["Born in the U.S.A.", "Bruce Springsteen", "1984", 810000, "8d51c27a-f538-3f2a-82e9-550596a38218"],
    ["Highway 61 Revisited", "Bob Dylan", "1965", 810000, "32752406-3063-3131-85b1-71a41d83804c"],
    ["Blonde on Blonde", "Bob Dylan", "1966", 790000, "8e0390c2-53e2-3774-9604-9850c5024c49"],
    ["Blood on the Tracks", "Bob Dylan", "1975", 780000, "99a9868c-2046-3f36-b62c-849a67a54908"],
    ["Random Access Memories", "Daft Punk", "2013", 880000, "aa997882-6c26-4120-95af-fae7774336bf"],
    ["Discovery", "Daft Punk", "2001", 860000, "5a800e26-846f-3c8c-b411-0f1585852589"],
    ["Back to Black", "Amy Winehouse", "2006", 850000, "84a4590e-652f-32d9-8380-f4875e422127"],
    ["21", "Adele", "2011", 860000, "65933730-1c4f-3506-b62c-0c473802f714"],
    ["Ten", "Pearl Jam", "1991", 850000, "f1ef77c6-c192-36c2-a791-505d90224740"],
    ["Californication", "Red Hot Chili Peppers", "1999", 860000, "3d00fb45-f8ab-3436-a8e1-b4bfc4d66914"],
    ["Blood Sugar Sex Magik", "Red Hot Chili Peppers", "1991", 830000, "e8f70201-8899-3f0c-9e07-5d6495bc8047"],
    ["(What’s the Story) Morning Glory?", "Oasis", "1995", 870000, "42428027-0723-3996-9614-6b0b50e72917"],
    ["Definitely Maybe", "Oasis", "1994", 820000, "5224c8c9-b538-3f13-9657-1a2961239a01"],
    ["Synchronicity", "The Police", "1983", 780000, "7e853800-87e1-3e20-a11d-25600a23f012"],
    ["Ten Summoner’s Tales", "Sting", "1993", 720000, "0215c27c-0101-3202-9a21-51a604224b0b"],
    ["The Dream of the Blue Turtles", "Sting", "1985", 680000, "202f36a8-c0c9-3029-a080-52340b712a97"],
    # Classici Italiani
    ["Crêuza de mä", "Fabrizio De André", "1984", 650000, "c7a6e068-1404-3925-801a-89f788904a6c"],
    ["Anime salve", "Fabrizio De André", "1996", 640000, "8224c8c9-b538-3f13-9657-1a2961239a02"],
    ["Storia di un impiegato", "Fabrizio De André", "1973", 610000, "8224c8c9-b538-3f13-9657-1a2961239a03"],
    ["Non al denaro non all'amore né al cielo", "Fabrizio De André", "1971", 600000, "8224c8c9-b538-3f13-9657-1a2961239a04"],
    ["La buona novella", "Fabrizio De André", "1970", 590000, "8224c8c9-b538-3f13-9657-1a2961239a05"],
    ["Fabrizio De André (Indiano)", "Fabrizio De André", "1981", 570000, "8224c8c9-b538-3f13-9657-1a2961239a06"],
    ["Rimini", "Fabrizio De André", "1978", 560000, "8224c8c9-b538-3f13-9657-1a2961239a07"],
    ["Volume 1", "Fabrizio De André", "1967", 550000, "8224c8c9-b538-3f13-9657-1a2961239a08"],
    ["Tutti morimmo a stento", "Fabrizio De André", "1968", 540000, "8224c8c9-b538-3f13-9657-1a2961239a09"],
    ["La voce del padrone", "Franco Battiato", "1981", 680000, "9224c8c9-b538-3f13-9657-1a2961239a10"],
    ["L'era del cinghiale bianco", "Franco Battiato", "1979", 580000, "9224c8c9-b538-3f13-9657-1a2961239a11"],
    ["Patriots", "Franco Battiato", "1980", 540000, "9224c8c9-b538-3f13-9657-1a2961239a12"],
    ["Fleurs", "Franco Battiato", "1999", 560000, "9224c8c9-b538-3f13-9657-1a2961239a13"],
    ["Gommalacca", "Franco Battiato", "1998", 530000, "9224c8c9-b538-3f13-9657-1a2961239a14"],
    ["L'imboscata", "Franco Battiato", "1996", 550000, "9224c8c9-b538-3f13-9657-1a2961239a15"],
    ["Lucio Dalla", "Lucio Dalla", "1979", 620000, "9224c8c9-b538-3f13-9657-1a2961239a16"],
    ["Come è profondo il mare", "Lucio Dalla", "1977", 630000, "9224c8c9-b538-3f13-9657-1a2961239a17"],
    ["Dalla", "Lucio Dalla", "1980", 580000, "9224c8c9-b538-3f13-9657-1a2961239a18"],
    ["Caruso", "Lucio Dalla", "1986", 570000, "9224c8c9-b538-3f13-9657-1a2961239a19"],
    ["Rimmel", "Francesco De Gregori", "1975", 620000, "9224c8c9-b538-3f13-9657-1a2961239a20"],
    ["La donna cannone", "Francesco De Gregori", "1983", 590000, "9224c8c9-b538-3f13-9657-1a2961239a21"],
    ["De Gregori", "Francesco De Gregori", "1978", 560000, "9224c8c9-b538-3f13-9657-1a2961239a22"],
    ["Titanic", "Francesco De Gregori", "1982", 550000, "9224c8c9-b538-3f13-9657-1a2961239a23"],
    ["Nero a metà", "Pino Daniele", "1980", 630000, "9224c8c9-b538-3f13-9657-1a2961239a24"],
    ["Pino Daniele", "Pino Daniele", "1979", 590000, "9224c8c9-b538-3f13-9657-1a2961239a25"],
    ["Terra mia", "Pino Daniele", "1977", 580000, "9224c8c9-b538-3f13-9657-1a2961239a26"],
    ["Vai mo'", "Pino Daniele", "1981", 600000, "9224c8c9-b538-3f13-9657-1a2961239a27"],
    ["Bollicine", "Vasco Rossi", "1983", 640000, "9224c8c9-b538-3f13-9657-1a2961239a28"],
    ["Siamo solo noi", "Vasco Rossi", "1981", 610000, "9224c8c9-b538-3f13-9657-1a2961239a29"],
    ["Vado al massimo", "Vasco Rossi", "1982", 590000, "9224c8c9-b538-3f13-9657-1a2961239a30"],
    ["C'è chi dice no", "Vasco Rossi", "1987", 600000, "9224c8c9-b538-3f13-9657-1a2961239a31"],
    ["Liberi liberi", "Vasco Rossi", "1989", 580000, "9224c8c9-b538-3f13-9657-1a2961239a32"],
    ["Gli spari sopra", "Vasco Rossi", "1993", 570000, "9224c8c9-b538-3f13-9657-1a2961239a33"],
    ["Anima latina", "Lucio Battisti", "1974", 630000, "9224c8c9-b538-3f13-9657-1a2961239a34"],
    ["Il mio canto libero", "Lucio Battisti", "1972", 650000, "9224c8c9-b538-3f13-9657-1a2961239a35"],
    ["Il nostro caro angelo", "Lucio Battisti", "1973", 600000, "9224c8c9-b538-3f13-9657-1a2961239a36"],
    ["Emozioni", "Lucio Battisti", "1970", 620000, "9224c8c9-b538-3f13-9657-1a2961239a37"],
    ["Lucio Battisti, la batteria, il contrabbasso, eccetera", "Lucio Battisti", "1976", 590000, "9224c8c9-b538-3f13-9657-1a2961239a38"],
    ["Una donna per amico", "Lucio Battisti", "1978", 580000, "9224c8c9-b538-3f13-9657-1a2961239a39"],
    ["Oro incenso & birra", "Zucchero", "1989", 610000, "9224c8c9-b538-3f13-9657-1a2961239a40"],
    ["Blue's", "Zucchero", "1987", 580000, "9224c8c9-b538-3f13-9657-1a2961239a41"],
    ["Buon compleanno Elvis", "Ligabue", "1995", 600000, "9224c8c9-b538-3f13-9657-1a2961239a42"],
    ["Ligabue", "Ligabue", "1990", 560000, "9224c8c9-b538-3f13-9657-1a2961239a43"],
    ["Storia di un minuto", "Premiata Forneria Marconi", "1972", 590000, "9224c8c9-b538-3f13-9657-1a2961239a44"],
    ["Per un amico", "Premiata Forneria Marconi", "1972", 580000, "9224c8c9-b538-3f13-9657-1a2961239a45"],
    ["Darwin!", "Banco del Mutuo Soccorso", "1972", 570000, "9224c8c9-b538-3f13-9657-1a2961239a46"],
    ["Banco del Mutuo Soccorso", "Banco del Mutuo Soccorso", "1972", 560000, "9224c8c9-b538-3f13-9657-1a2961239a47"],
    ["Arbeit macht frei", "Area", "1973", 550000, "9224c8c9-b538-3f13-9657-1a2961239a48"],
    ["Felona e Sorona", "Le Orme", "1973", 550000, "9224c8c9-b538-3f13-9657-1a2961239a49"],
    ["Profondo rosso", "Goblin", "1975", 560000, "9224c8c9-b538-3f13-9657-1a2961239a50"],
    ["Radici", "Francesco Guccini", "1972", 580000, "9224c8c9-b538-3f13-9657-1a2961239a51"],
    ["Via Paolo Fabbri 43", "Francesco Guccini", "1976", 570000, "9224c8c9-b538-3f13-9657-1a2961239a52"],
    ["L'isola non trovata", "Francesco Guccini", "1970", 540000, "9224c8c9-b538-3f13-9657-1a2961239a53"],
    ["Il cielo è sempre più blu", "Rino Gaetano", "1975", 580000, "9224c8c9-b538-3f13-9657-1a2961239a54"],
    ["Mio fratello è figlio unico", "Rino Gaetano", "1976", 590000, "9224c8c9-b538-3f13-9657-1a2961239a55"],
    ["Aida", "Rino Gaetano", "1977", 580000, "9224c8c9-b538-3f13-9657-1a2961239a56"],
    ["Nuntereggae più", "Rino Gaetano", "1978", 570000, "9224c8c9-b538-3f13-9657-1a2961239a57"],
    ["Paolo Conte", "Paolo Conte", "1974", 550000, "9224c8c9-b538-3f13-9657-1a2961239a58"],
    ["Un gelato al limon", "Paolo Conte", "1979", 560000, "9224c8c9-b538-3f13-9657-1a2961239a59"],
    ["Paris milonga", "Paolo Conte", "1981", 550000, "9224c8c9-b538-3f13-9657-1a2961239a60"],
    ["Aguaplano", "Paolo Conte", "1987", 540000, "9224c8c9-b538-3f13-9657-1a2961239a61"],
    ["Hanno ucciso l'Uomo Ragno", "883", "1992", 590000, "9224c8c9-b538-3f13-9657-1a2961239a62"],
    ["Nord sud ovest est", "883", "1993", 580000, "9224c8c9-b538-3f13-9657-1a2961239a63"],
    ["...Squérez?", "Lùnapop", "1999", 590000, "9224c8c9-b538-3f13-9657-1a2961239a64"],
    ["Microchip emozionale", "Subsonica", "1999", 570000, "9224c8c9-b538-3f13-9657-1a2961239a65"],
    ["Hai paura del buio?", "Afterhours", "1997", 570000, "9224c8c9-b538-3f13-9657-1a2961239a66"],
    ["Catartica", "Marlene Kuntz", "1994", 550000, "9224c8c9-b538-3f13-9657-1a2961239a67"],
    ["Desaparecido", "Litfiba", "1985", 560000, "9224c8c9-b538-3f13-9657-1a2961239a68"],
    ["17 re", "Litfiba", "1986", 570000, "9224c8c9-b538-3f13-9657-1a2961239a69"],
    ["El Diablo", "Litfiba", "1990", 580000, "9224c8c9-b538-3f13-9657-1a2961239a70"],
    ["Terremoto", "Litfiba", "1993", 570000, "9224c8c9-b538-3f13-9657-1a2961239a71"],
    ["Verità supposte", "Caparezza", "2003", 580000, "9224c8c9-b538-3f13-9657-1a2961239a72"],
    ["Habemus Capa", "Caparezza", "2006", 550000, "9224c8c9-b538-3f13-9657-1a2961239a73"],
    ["Prisoner 709", "Caparezza", "2017", 560000, "9224c8c9-b538-3f13-9657-1a2961239a74"],
    ["Mina Celentano", "Mina & Adriano Celentano", "1998", 600000, "9224c8c9-b538-3f13-9657-1a2961239a75"],
    ["Studio Collection", "Mina", "1965", 550000, "9224c8c9-b538-3f13-9657-1a2961239a76"],
    ["Questo piccolo grande amore", "Claudio Baglioni", "1972", 590000, "9224c8c9-b538-3f13-9657-1a2961239a77"],
    ["La vita è adesso", "Claudio Baglioni", "1985", 600000, "9224c8c9-b538-3f13-9657-1a2961239a78"],
    ["Sotto il segno dei pesci", "Antonello Venditti", "1978", 590000, "9224c8c9-b538-3f13-9657-1a2961239a79"],
    ["Cuore", "Antonello Venditti", "1984", 580000, "9224c8c9-b538-3f13-9657-1a2961239a80"],
    ["Mainstream", "Calcutta", "2015", 550000, "9224c8c9-b538-3f13-9657-1a2961239a81"],
    ["Teatro d'ira - Vol. I", "Måneskin", "2021", 620000, "9224c8c9-b538-3f13-9657-1a2961239a82"],
    ["Il ballo della vita", "Måneskin", "2018", 580000, "9224c8c9-b538-3f13-9657-1a2961239a83"],
    ["Rush!", "Måneskin", "2023", 570000, "9224c8c9-b538-3f13-9657-1a2961239a84"],
    ["Persona", "Marracash", "2019", 580000, "9224c8c9-b538-3f13-9657-1a2961239a85"],
    ["Noi, loro, gli altri", "Marracash", "2021", 570000, "9224c8c9-b538-3f13-9657-1a2961239a86"],
    ["In una stanza con poca luce", "Giovanni Allevi", "2005", 500000, "9224c8c9-b538-3f13-9657-1a2961239a87"],
    ["No Concept", "Giovanni Allevi", "2005", 530000, "9224c8c9-b538-3f13-9657-1a2961239a88"],
    ["Joy", "Giovanni Allevi", "2006", 520000, "9224c8c9-b538-3f13-9657-1a2961239a89"],
    ["Evolution", "Giovanni Allevi", "2008", 510000, "9224c8c9-b538-3f13-9657-1a2961239a90"],
    ["Le onde", "Ludovico Einaudi", "1996", 560000, "9224c8c9-b538-3f13-9657-1a2961239a91"],
    ["Divenire", "Ludovico Einaudi", "2006", 580000, "9224c8c9-b538-3f13-9657-1a2961239a92"],
    ["Una mattina", "Ludovico Einaudi", "2004", 570000, "9224c8c9-b538-3f13-9657-1a2961239a93"],
    ["In a Time Lapse", "Ludovico Einaudi", "2013", 580000, "9224c8c9-b538-3f13-9657-1a2961239a94"],
]


def build_music():
    print("== MUSICA ==", flush=True)
    entries = {}
    for item in CURATED_ALBUMS:
        mbid = item[4] if not item[4].startswith(("8224c8c9", "9224c8c9")) else ""
        entries[f"{item[0].lower()}|{item[1].lower()}"] = [item[0], item[1], item[2], item[3], mbid]

    # 1) Album più ascoltati in assoluto (statistiche pubbliche ListenBrainz)
    for rng in ("all_time", "this_year"):
        for off in range(0, 1000, 100):
            d = http_json(f"https://api.listenbrainz.org/1/stats/sitewide/release-groups?range={rng}&count=100&offset={off}")
            time.sleep(0.25)
            if not d:
                continue
            for rg in d.get("payload", {}).get("release_groups", []):
                rgid = rg.get("release_group_mbid") or ""
                title = (rg.get("release_group_name") or "").strip()
                artist = (rg.get("artist_name") or "").strip()
                if not title or not artist:
                    continue
                k = f"{title.lower()}|{artist.lower()}"
                pop = int(rg.get("listen_count") or 0)
                if k not in entries or pop > entries[k][3]:
                    entries[k] = [title, artist, "", pop, rgid]
    print(f"  album sitewide + curati: {len(entries)}", flush=True)

    # 2) Query Lucene in batch per gli artisti italiani (10 artisti per query = 16 richieste totali)
    candidates = {}
    chunk_size = 8
    for i in range(0, len(ITALIAN_ARTISTS), chunk_size):
        chunk = ITALIAN_ARTISTS[i:i + chunk_size]
        or_clause = " OR ".join(f'artist:"{a}"' for a in chunk)
        lucene = f"({or_clause}) AND primarytype:album AND status:official NOT secondarytype:live"
        url = f"https://musicbrainz.org/ws/2/release-group/?query={urllib.parse.quote(lucene)}&fmt=json&limit=100"
        d = http_json(url)
        time.sleep(1.05)
        if not d:
            continue
        for rg in d.get("release-groups", []):
            rgid = rg.get("id") or ""
            title = (rg.get("title") or "").strip()
            credit = rg.get("artist-credit") or []
            artist = "".join((c.get("name", "") + (c.get("joinphrase") or "")) for c in credit).strip()
            if not title or not artist or not rgid:
                continue
            yr = year_of(rg.get("first-release-date"))
            rcount = int(rg.get("count", 1) or 1)
            candidates[rgid] = [title, artist, yr, rcount * 500, rgid]
    print(f"  album italiani da MusicBrainz: {len(candidates)}", flush=True)

    # 3) Popolarità in batch (endpoint pubblico ListenBrainz)
    ids = list(candidates.keys())
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        body = json.dumps({"release_group_mbids": batch}).encode()
        d = http_json("https://api.listenbrainz.org/1/popularity/release-group", data=body,
                      headers={"Content-Type": "application/json"})
        time.sleep(0.25)
        if isinstance(d, list):
            for row in d:
                rgid = row.get("release_group_mbid")
                if rgid in candidates:
                    pop = int(row.get("total_listen_count") or 0)
                    if pop > candidates[rgid][3]:
                        candidates[rgid][3] = pop

    for rgid, e in candidates.items():
        k = f"{e[0].lower()}|{e[1].lower()}"
        if k in entries:
            if not entries[k][2] and e[2]:
                entries[k][2] = e[2]
            if e[4] and (
                not entries[k][4]
                or entries[k][4].startswith(("9224c8c9", "8224c8c9"))
            ):
                entries[k][4] = e[4]
            continue
        entries[k] = e

    out = sorted(entries.values(), key=lambda e: -e[3])
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "seed_music.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "ListenBrainz/MusicBrainz (CC0)", "built": time.strftime("%Y-%m-%d"),
                   "fields": ["title", "artist", "year", "popularity", "mbid"], "items": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"  salvati {len(out)} album in {path}", flush=True)


# ---------------------------------------------------------------- LIBRI
def build_books():
    print("== LIBRI ==", flush=True)
    fields = ("key,title,author_name,first_publish_year,edition_count,"
              "editions,editions.title,editions.publisher,editions.publish_date,editions.language")
    entries = {}
    plan = [("ita", "it", 45), ("eng", "en", 20)]
    for lang3, lang2, pages in plan:
        for page in range(1, pages + 1):
            url = ("https://openlibrary.org/search.json?q=" + urllib.parse.quote(f"language:{lang3}") +
                   f"&sort=editions&lang={lang2}&limit=100&page={page}&fields={fields}")
            d = http_json(url, timeout=90)
            time.sleep(1.0)
            if not d:
                continue
            for doc in d.get("docs", []):
                key = doc.get("key")
                authors = doc.get("author_name") or []
                if not key or not authors:
                    continue
                ed = ((doc.get("editions") or {}).get("docs") or [{}])[0]
                langs = ed.get("language") or []
                title = ed.get("title") if lang3 in langs and ed.get("title") else doc.get("title")
                if not title:
                    continue
                pub = (ed.get("publisher") or [""])[0] if lang3 in langs else ""
                eyear = year_of((ed.get("publish_date") or [""])[0]) if lang3 in langs else ""
                ident = f"{key}|{lang3}"
                if ident in entries:
                    continue
                entries[ident] = [title, authors[0], str(doc.get("first_publish_year") or ""),
                                  int(doc.get("edition_count") or 0), key, pub, eyear, lang3]
            print(f"  {lang3} pagina {page}/{pages}: {len(entries)} opere", flush=True)

    out = sorted(entries.values(), key=lambda e: -e[3])
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "seed_books.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "Open Library", "built": time.strftime("%Y-%m-%d"),
                   "fields": ["title", "author", "first_year", "popularity", "work_key",
                              "publisher", "edition_year", "lang"], "items": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"  salvate {len(out)} opere in {path}", flush=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "music"):
        build_music()
    if what in ("all", "books"):
        build_books()
