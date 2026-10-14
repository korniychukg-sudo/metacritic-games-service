import time

from app.similarity import GameFeatures, compute_similar, tokenize


def make_games():
    return [
        GameFeatures(1, "Star Wars: Galactic Racer", "High speed podracing across the galaxy with boost and drift.",
                     ["Future Racing"], ["Star Wars"], ["Fuse Games"], ["PC", "PlayStation 5"]),
        GameFeatures(2, "Star Wars Episode I: Racer", "Classic podracing on Tatooine at blistering speed.",
                     ["Future Racing"], ["Star Wars"], ["LucasArts"], ["PC"]),
        GameFeatures(3, "Planet Zoo 2", "Build and manage a zoo, care for animals and design habitats.",
                     ["Management"], [], ["Frontier"], ["PC"]),
        GameFeatures(4, "Planet Coaster 3", "Build theme parks, design roller coasters and manage guests.",
                     ["Management"], [], ["Frontier"], ["PC"]),
        GameFeatures(5, "Gears of War: E-Day", "Brutal third-person cover shooter against the Locust horde.",
                     ["Third-Person Shooter"], ["Gears of War"], ["The Coalition"], ["Xbox Series X", "PC"]),
    ]


def test_franchise_and_developer_matches_rank_first():
    sim = compute_similar(make_games(), top_n=5)
    assert sim[1][0]["id"] == 2
    assert "та же франшиза" in sim[1][0]["reasons"]
    assert sim[3][0]["id"] == 4
    assert "тот же разработчик" in sim[3][0]["reasons"]


def test_self_excluded_and_top_n_respected():
    sim = compute_similar(make_games(), top_n=2)
    for gid, lst in sim.items():
        assert gid not in [x["id"] for x in lst]
        assert len(lst) <= 2
        scores = [x["score"] for x in lst]
        assert scores == sorted(scores, reverse=True)


def test_unrelated_game_may_have_no_matches_and_small_db():
    sim = compute_similar(make_games())
    assert all(x["id"] != 5 for x in sim[3])
    assert compute_similar(make_games()[:1]) == {1: []}


def test_tokenize_drops_stopwords():
    toks = tokenize("The game features new worlds and racing races")
    assert "the" not in toks and "game" not in toks
    assert "racing" in toks and "race" in toks


def test_scales_to_a_few_thousand_games():
    genres = ["Action", "RPG", "Racing", "Puzzle", "Strategy", "Shooter"]
    words = "dragon sword space ship race car puzzle tower city build farm zombie ninja robot magic castle".split()
    games = [GameFeatures(i, f"Game {i}", " ".join(words[(i * k) % len(words)] for k in range(1, 12)),
                          [genres[i % len(genres)]], [], [f"Studio {i % 300}"], ["PC"]) for i in range(1500)]
    t = time.time()
    sim = compute_similar(games)
    assert len(sim) == 1500
    assert time.time() - t < 60
