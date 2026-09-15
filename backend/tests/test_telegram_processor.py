"""Tests unitarios de deduplicación de picks (funciones puras).

No tocan la base de datos: solo cubren las heurísticas de similitud
usadas por `_find_duplicate_pick`.
"""

from app.services.telegram.processor import _text_similarity, _word_set_similarity


class TestWordSetSimilarity:
    def test_mismas_palabras_en_distinto_orden_son_muy_similares(self):
        similarity = _word_set_similarity("Real Madrid gana", "GANA REAL MADRID")
        assert similarity >= 0.85

    def test_palabras_distintas_tienen_baja_similitud(self):
        similarity = _word_set_similarity(
            "Titouan Droguet gana", "Nicolai Budkov Kjaer gana"
        )
        assert similarity < 0.85

    def test_texto_vacio_no_es_similar(self):
        assert _word_set_similarity("", "Real Madrid gana") == 0.0


class TestTextSimilarity:
    def test_textos_identicos_son_totalmente_similares(self):
        assert _text_similarity("Real Madrid gana", "Real Madrid gana") == 1.0
