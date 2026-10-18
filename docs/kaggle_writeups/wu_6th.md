# [6th] DPC solution - 15 models ensemble on diverse data sources
https://www.kaggle.com/competitions/deep-past-initiative-machine-translation/discussion/684231

First of all, I’d like to thank my teammate [@jackvd](https://www.kaggle.com/jackvd) for providing high-quality data and clever data cleaning methods, as well as for constantly bringing fresh ideas to the table. I also want to thank [@marisyn](https://www.kaggle.com/marisyn) for providing numerous excellent APIs and handling the PDF extraction work! And all personnel from the host organization, DPI.

Our solution focuses on three key areas: data, training, and inference.

# 1. Data Extract

## PDF Data

The organizers provided a large number of [PDFs](https://www.kaggle.com/datasets/deeppast/old-assyrian-kltepe-tablets-in-pdf) (There is also a *Larsen 2002* in [here](https://www.kaggle.com/datasets/deeppast/old-assyrian-grammars-and-other-resources).), so our first challenge was figuring out how to effectively extract text from them.

After reviewing these PDFs, we have concluded that they can primarily be categorized into three types: image, text, and broken_text:

- **Image type**: Although these have an OCR text layer, OCR artifacts become clearly visible when zooming in on the PDF. This category includes: AKT 1 1990, AKT 11a, AKT 2 1995, AKT 3 1995, AKT 4 2006, AKT 6a, AKT 6b, AKT 6c, AKT 6d, AKT 6e, and AKT_12.

- **Text type**: Zooming in does not degrade the clarity of the PDF in any way. Furthermore, the text layer extracted using PyMuPDF has an exact one-to-one correspondence with what is visible to the naked eye. For example, using this code to handle one-to-one character error issues:
```python
# AKT 5 2008
_MACRON = {
    "a": "ā", "e": "ē", "i": "ī", "u": "ū",
    "A": "Ā", "E": "Ē", "I": "Ī", "U": "Ū",
}
_MARKERS_CLASS = r"[\u00a3\u2039\u2122]+"          # £‹™ (allow multi)
_MARKER_VOWEL_RE = re.compile(_MARKERS_CLASS + r"([aeiuAEIU])")

def apply_akt5_vowel_marker_rule(text: str) -> str:
    text = _MARKER_VOWEL_RE.sub(lambda m: _MACRON[m.group(1)], text)
    text = re.sub(_MARKERS_CLASS, "", text)
    return text
```
This category includes: AKT 10, AKT 11b, AKT 5 2008, AKT 7b, and AKT 9a.

- **Broken_text type**: Similar to the text type, zooming in does not affect clarity. However, the text layer extracted using PyMuPDF exhibits a one-to-many or many-to-many correspondence with what is visually observed. 

![example AKT 8 2015](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F18505507%2F05787bbe3e387d112d2845c6df35a3f0%2F4.png?generation=1774319930773772&alt=media)


This category includes: AKT 8 2015 and AKT 7a.

For the broken_text type, we will directly treat them as the image type.

Once the file type is identified, we pass text-based PDFs directly to the LLM API for processing, where it extracts the corresponding document pairs (which are later split into sentences). The method involves using Regular Expressions (Regex) to identify heading blocks, then passing both the headings and their corresponding text blocks to a LLM api for analysis and the extraction of document pairs (including error correction).

For image-based PDFs, our workflow is as follows: 
we first utilize the [open-source GLM-OCR method](https://www.kaggle.com/code/angantyr/text-extraction-from-pdf-docs-using-glm-oc), featuring custom parameter tuning and parallelization enhancements. Then we can get this:
```python
{
    "p-1_f-0": "1. kt 94/k 1263",
    "p-1_f-1": "14 1/2 GÍN KB "KI" I-lí-dan",
    "p-1_f-2": "14 1/2 shekels of silver is owed by Ili-dan",
    "p-1_f-3": "DUMU Šu-ki-tim 2 ma-na 10 GÍN / KU",
    "p-1_f-4": "son of Sukkutum. 2 minas 10 shekels of",
    ...
}
```
|    |   page_num |   field_num |    x |   y |    w |    h |   x_pad |   y_pad |   w_pad |   h_pad |
|---:|-----------:|------------:|-----:|----:|-----:|-----:|--------:|--------:|--------:|--------:|
|  0 |          1 |          -1 |    0 |   0 | 2075 | 2809 |       0 |       0 |    2075 |    2809 |
|  1 |          1 |           0 |  864 | 253 |  378 |   50 |     852 |     241 |     402 |      74 |
|  2 |          1 |           1 |  166 | 403 |  533 |   48 |     154 |     391 |     557 |      72 |
|  3 |          1 |           2 | 1167 | 410 |  746 |   52 |    1155 |     398 |     770 |      76 |
|  4 |          1 |           3 |  162 | 470 |  773 |   49 |     150 |     458 |     797 |      73 |
|  ... |

 

Next, we use Regex to match the headings again. The LLM API then processes these headings and images to generate text. Finally, we run a second pass with the image and text together for fine-grained correction, ensuring a high degree of accuracy.

Some of these PDFs are in Turkish. We're using a combination of LLM APIs with dictionaries & Turkish translation to translate them into English for training.

---

## Train.csv
Regarding `train.csv`, I have to mention the **OARE** website. For each sample, we visit `https://oare.byu.edu/epigraphies/{oare_id}`. 

We discovered that clicking on a specific word on this page displays its definition. Consequently, we **scraped** the site to obtain the meanings of these words. While not every word has a corresponding definition, the vast majority do, which is more than sufficient for our needs.

As a result, we obtained a dictionary covering most of the words in train.csv. 

However, it is important to note that almost all documents in train.csv originate from AKT 5 2008, AKT 6a, AKT 6b, AKT 6c, AKT 6d, AKT 6e, and AKT 8 2015. To improve accuracy, we performed similarity matching between the entries in train.csv and the text extracted from these PDF sources. This allowed us to retrieve the "ground truth" or correct form of the documents, as the data quality within the original PDFs is significantly higher. (This involved a lot of manual fixes.)

Following these procedures, we used that dictionary to give the LLM a general understanding of what transliteration means, then followed the English sentence breaks to eventually obtain sentence-level parallel corpora. (The alignment in this step might not be perfect, so we have implemented corresponding AUG to address this.)

---

## Published_texts.csv

Similar to train.csv, almost all samples can be found on the OARE website. However, since translations were unavailable, we used an LLM API to create pseudo-labels for these samples. The process is roughly as follows: we prompt the LLM to translate the original text using the dictionary we retrieved from the OARE site.

example (The prompt also includes several constraints, such as JSON formatting, mana 60 conversion, ...):
```
key_prompt = """
KIŠIB a-šùr-ma-lik DUMU i-na-a a-na a-šùr-na-da DUMU   ù en-um-a-šùr DUMU   a-pu-tum a-na a-wa-at ṭup-pì-im iḫ-da   ša ú-  ḫa-sí-sú  

{KIŠIB|form:Noun|1. seal, cylinder seal 2. seal impression produced by a cylinder seal 3. sealed clay tablet (legal or administrative document, also letter)}
{a-šùr-ma-lik|form:Noun|1. Aššur is counselor}
{DUMU|form:Noun|1. son}
{i-na-a|form:Noun|nan}
{a-na|form:Preposition|1. to, for, up to,  toward, against, upon}
{a-šùr-na-da|form:Noun|1. Praise Aššur!}
{ù|form:Conjunction|1. and}
{en-um-a-šùr|form:Noun|1. Mercy O Aššur}
{a-pu-tum|form:Interjection|1. urgent!, please!}
{a-wa-at|form:Noun|1. word, matter, lawsuit 2. pl. awâtum - stipulation, rule}
{ṭup-pì-im|form:Noun|1. (inscribed) tablet (of clay, rarely of other materials) 2. board, flat surface}
{iḫ-da|form:Finite verb|1. to attend, to watch, to pay attention, to do something carefully 2. to draw someone's attention to something, to alert, to ask someone to pay attention, to take care}
{ša|form:Pronoun|1. relative pronoun}
"""
```

---

## Publications.csv

For this file, we provide a fixed number of pages to the LLM API to extract sentences and other elements. While this yields a significant amount of data, it is difficult to achieve document-level alignment.

---

With that, we have obtained a parallel corpus dataset; the remaining task is now focused on data cleaning.

# 2. Data Processing

Actually, the organizers updated the data several times. The `train.csv` I used in Step 1 to match against the PDF data was the second version, which was much better normalized.

For preprocessing, here’s the main stuff (mainly for pdf-data or LLM results, The second version of train.csv is already quite clean.):

**General Foundation Processing (Applied to Both Sides)**
- Character-level normalization: Apply Unicode NFKC normalization, remove zero-width characters, and unify the formatting of dashes and hyphens.
- Special letter simplification: Convert special characters with diacritics (e.g., Ḫ, ḫ) to standard letters (e.g., H, h).
- Removal of academic editorial symbols: Eliminate various special editorial brackets commonly found in the literature (e.g., ⸢ ⸣ ⌈ ⌉ ⟦ ⟧ [ ], etc.) and footnote superscript numbers.
- Fraction and decimal formatting: Intelligently convert decimals, mixed numbers (e.g., 1 1/2), or regular fractions in the text into compact Unicode single-character fractions (e.g., ½, ⅓, ¾).

---

**Transliteration Text Side**
The primary objective of the source text side is to eliminate typographical variations within the sign system and to standardize determinatives for divine names, toponyms, and similar entities.
- Determinative and script normalization:
  - Convert subscript numbers (e.g., ₀₁₂) to standard digits.
  - Unify superscript determinatives (e.g., ᵏⁱ, ᵈUTU) or superscripts indicated by carets (e.g., ^{ki}) into standard curly brace syntax (e.g., {ki}, {d}UTU).
  - Intelligently identify and merge determinatives that are separated from proper nouns (e.g., d UTU -> {d}UTU).
- Sign composition and placeholder cleanup:
  - Convert multiplication signs (×, ✖) representing sign compounding into periods (.).
  - Clean up or convert the unknown sign marker "x" attached to the end of a word or before a parenthesis (e.g., SUDx -> SUD).
- Removal of specific academic annotations: Remove specifically formatted parenthetical annotations (e.g., editorial notes like "(over erased)", "(met.)"), standalone line numbers (e.g., "10."), and the pipe character (|) unique to the Oracc system.
- Gap handling and alignment: Unify and normalize various symbols indicating missing text (e.g., consecutive periods "...", the standalone letter "x", "missing", etc.) into a single placeholder ` `, and strictly clean up extraneous spaces and punctuation before and after the ` ` marker.
- Dictionary-level hard replacement: Erase punctuation marks such as question marks and exclamation points based on the `transliteration_map`, and correct specific spellings of personal names and anomalous annotations.

---

**Translation Side**
The primary objective of the translation side is to enhance the natural linguistic fluency of the text, eliminating alternative options and academic jargon left by translators.
- Aggressive annotation and markup clearance: Compared to the source text side, the translation side indiscriminately removes all translational explanations within parentheses, while also eliminating asterisks (*) and quotation marks (") used for emphasis.
- Academic/scribal terminology removal: Automatically identify and delete academic annotations such as "fem. plur." (feminine plural), "lit." (literally), "sic" (thus in the original), "i.e.", and similar terms.
- Intelligent decision-making for slash alternatives:
  - When an "either/or" scenario appears in the translation (e.g., a / the, like / instead, promised / been), the script automatically selects the vocabulary that better fits the context or predefined settings.
  - Automatically handle capitalization inheritance (if the original word is capitalized, the selected word will automatically be capitalized).
- Roman numeral month conversion: Identify expressions such as "Month XII" and convert them to Arabic numerals, such as "Month 12".
- Expansion of complex measurement units: Based on the `translation_map`, expand complex fractional weights unique to antiquity (e.g., 12 5/12 shekels) into more readable text (e.g., 12 ⅓ shekels 15 grains).
- Punctuation and typographical correction: Fix extraneous spaces before punctuation marks (e.g., commas, semicolons) and ensure compactness between numbers and fractions.
- Gap mapping: Similarly, replace "x" placeholders or ellipses appearing in the translation with ` ` to ensure strict alignment with the source text side in subsequent machine learning or text comparison processes.

---

Actually, this part skips over a considerable amount of manual effort—things like manually checking for anomalies, using regex for matching, and then incorporating those into the cleaning rules, this is our final clean func:

[clean function](https://www.kaggle.com/code/wscmisx/dpc-clean-function/notebook?scriptVersionId=306066643)

# 3. Training
- **Dynamic Context Sampling**

This technique aims to address two primary issues:

1. Potential sentence misalignments during the extraction phase; we intend to make the model less sensitive to sentence alignment.
2. The unknown sentence length distribution of the test set, which may differ from our segmented data; we utilize this approach to enable the model to adapt to varying input and output lengths.

Specifically, we implemented the following mechanisms:

- **Zipf / Geometric Mixture Distribution Sampling (K)**: The number of contextual sentences included is dynamically determined for each instance . Following Zipf's law (long-tail distribution), the model predominantly encounters short contexts and occasionally processes extremely long contexts, thereby enhancing its adaptability to varying text lengths.
- **Symmetric / Asymmetric Allocation (Symmetric LR)**: The ratio of left to right contextual sentences (L and R) is dynamically determined.
- **Random Contextual Sentence Insertion (Random Context)**: There is a 20% probability (`random_ctx_prob = 0.20`) of bypassing contiguous context and instead randomly sampling and concatenating sentences from the same article. This compels the model to learn not only the local context but also the global topic, preventing the rote memorization of absolute sentence orders.
```
def _build_k_cdf(self):
    """
    Build a CDF over K in [1, k_max].
    - zipf:     P(K) ∝ 1/K  (heavy-tail, good length coverage)
    - geom_mix: (1-α)·Geom(p) + α·Zipf(1/K)
    """
    keys = list(range(1, self.k_max + 1))

    if self.k_sampling == "geom_mix":
        # geometric over {1..k_max}: p*(1-p)^(k-1)
        geom, zipf = [self.geom_p * ((1.0 - self.geom_p) ** (k - 1)) for k in keys], [1.0 / k for k in keys]
        probs = [(1.0 - self.long_tail_mix) * g + self.long_tail_mix * z for g, z in zip(geom, zipf)]
    else:
        # default: Zipf / 1-K
        probs = [1.0 / k for k in keys]

    s = sum(probs); probs = [p / s for p in probs]
    cdf = list(accumulate(probs))
    cdf[-1] = 1.0
    return keys, cdf

def _sample_K(self):
    r = random.random()
    i = bisect_left(self._k_cdf, r)
    if i >= len(self._k_keys): i = len(self._k_keys) - 1
    return int(self._k_keys[i])

@staticmethod
def _sample_LR(K: int):
    # L ~ Binomial(K-1, 0.5), R = K-1-L
    if K  

Initially, we thought the extra data was too noisy and left it out of the formal training phase, but it actually performed better once we included it—probably just because of the large volume.

# 5. Inference

Our final submission consists of 15 models, primarily detailed as follows:

| Model Description | LB | PB |
| :--- | :--- | :--- |
| Large model trained on data subset v3 | 38.7 | 38.9 |
| Large model trained on data subset v3 with name augmentation | 37.8 | 38.8 |
| XL model trained on data subset v3 | 37.9 | 38.3 |
| Large model trained on data subset v4 | 38.6 | 39.5 |
| Large model trained on data subset v5 | 38.9 | 39.3 |
| Base model trained on data subset v5 | 38.8 | 39.0 |
| Base model trained on data subset v6 | 39.2 | 39.5 |
| Base model trained on data subset v6 with additional filtering and cleaning (v1) | 39.3 | 39.9 |
| Base model trained on data subset v6 with additional filtering and cleaning (v2) | 38.3 | 39.2 |
| Base model trained on data subset v6 with additional filtering and cleaning (v3) | 38.2 | 39.5 |
| Base model trained on data subset v6 with additional filtering and cleaning (v4) | 38.4 | 39.6 |

We performed 5-fold cross-validation training based on our best-performing model on the Public Leaderboard (LB score 39.3: Base model trained on data subset v6 with additional filtering and cleaning (v1)). Although these models were not submitted individually, they were also incorporated into the MBR (Minimum Bayes Risk) ensemble.

To accelerate inference, we restricted the output to four results per model using a beam size of 4 (`beam=4`), without applying any sampling methods (our experiments indicated that utilizing sampling within the MBR process leads to a decrease in score). 

The models were grouped and deployed across two T4 GPUs for execution.

![15models](https://www.googleapis.com/download/storage/v1/b/kaggle-user-content/o/inbox%2F18505507%2F2d32d5e4df1b2bbafc1e969535c8f85f%2Fall_models.png?generation=1774331390404829&alt=media)

[**Final Score:** 40.7  |  **Execution Time:** 208m 50s](https://www.kaggle.com/code/wscmisx/final-dpc-ensemble-mbr-infer)


# 6. What didn't work

- **Organizing the current target sentence and its context like this (during both training and inference):**
```text
translate Akkadian to English. Translate TARGET only. Use CONTEXT only as reference.
TARGET:
ki-ma mup-pì-ni ta-áa-me-a-ni a-ma-kam lu a-na aí-mì-im a-na É.GAL-lim i-dí-in lu té-ra-at É.GAL-lim ú-kà-lim lu na-aí-ma a-dí-ni lá i-dí-in ma-lá KÙ.AN na-áa-ú ni-bi„-it a-aí-im au-um-au ú au-mì a-bi„-au i-na mup-pì-im lu-up-ta-nim-ma ia-tí aí-ip-ri-ni aé-bi„-lá-nim

CONTEXT BEFORE:
um-ma kà-ru-um kà-ni-ia-ma a-na aa-qí-il… da-tim aí-ip-ri-ni kà-ar kà-ar-ma ú wa-bar-ra-tim qí-bi„-ma mup-pu-um aa a-lim(ki) i-li-kam i-na mup-pì-im aa a-lim(ki) ia-tù u„-mì-im a-nim ma-ma-an KÙ.AN i-aa-ú-mu-ni i-na né-mì-lim da-aùr ú-lá e-WA ia-ra-tí-au kà-ru-um kà-ni-ia i-lá-qé

CONTEXT AFTER:
me-+e-er mup-pì-ni a-na kà-ar kà-ar-ma ú wa-bar-ra-tim aé-bi„-lá KÙ.AN lu a-na DAM.GÀR-ru-tim i-dí-in au-mì a-wi-lim lu-up-ta-nim
```

- **Data augmentation on names and dictionaries in the source text during training**
Randomly altering the capitalization of names, and using a dictionary to randomly select a `norm` form for many-to-one `norm` -> `lexeme` mappings. 
The single-model score didn't improve much, but we still included these in our ensemble.

- **Weight fusion / averaging**
This showed almost no improvement and even resulted in lower scores than the original single model. After a few attempts, we stopped trying this and switched to using MBR (Minimum Bayes Risk) for ensembling instead.

- **Setting a very long `maxlen` and training at the document level**
Unsurprisingly, the results were poor because the test set is evaluated at the sentence level.

- **Using Large Language Models (Qwen, LLaMA, etc.)**
This was likely due to the tokenizer or the fact that my data wasn't clean enough in the early stages of the competition. Without applying any special tricks, the performance of a trained 14B model was only average. However, I was very glad to see top-ranking participants successfully taking on the challenge using LLMs—an excellent solution!

- **Bidirectional training (adding `akk->eng`)**
There wasn't much progress in terms of single-model improvement. We didn't have extra submission quotas to test this before ensembling, but perhaps including it in the ensemble could have provided a slight boost to the final results.

