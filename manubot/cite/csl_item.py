"""Represent bibliographic information for a single publication.

From the CSL docs:

    Next up are the bibliographic details of the items you wish to cite: the item metadata.

    For example, the bibliographic entry for a journal article may show the names of the
    authors, the year in which the article was published, the article title, the journal
    title, the volume and issue in which the article appeared, the page numbers of the
    article, and the article’s Digital Object Identifier (DOI). All these details help
    the reader identify and find the referenced work.

    Reference managers make it easy to create a library of items. While many reference
    managers have their own way of storing item metadata, most support common bibliographic
    exchange formats such as BibTeX and RIS. The citeproc-js CSL processor introduced a
    JSON-based format for storing item metadata in a way citeproc-js could understand.
    Several other CSL processors have since adopted this “CSL JSON” format (also known as
    “citeproc JSON”).

-- https://github.com/citation-style-language/documentation/blob/master/primer.txt

The terminology we've adopted is csl_data for a list of csl_item dicts, and csl_json
for csl_data that is JSON-serialized.
"""

import copy
import logging
import re

from manubot.cite.citekey import standardize_citekey, infer_citekey_prefix, is_valid_citekey


class CSL_Item(dict):
    """
    CSL_Item represents bibliographic information for a single citeable work.

    On a technical side CSL_Item is a Python dictionary with extra methods
    that help cleaning and manipulating it.

    These methods relate to:
    - adding an `id` key and value for CSL item
    - correcting bibliographic information and its structure
    - adding and reading a custom note to CSL item

    More information on CSL JSON (a list of CSL_Items) is available at:
    - https://citeproc-js.readthedocs.io/en/latest/csl-json/markup.html
    - http://docs.citationstyles.org/en/1.0.1/specification.html#standard-variables
    - https://github.com/citation-style-language/schema/blob/master/csl-data.json
    """

    # The ideas for CSL_Item methods come from the following parts of code:
    #  - [ ] citekey_to_csl_item(citekey, prune=True)
    # The methods in CSL_Item class provide primitives to reconstruct
    # fucntions above.

    type_mapping = {
        'journal-article': 'article-journal',
        'book-chapter': 'chapter',
        'posted-content': 'manuscript',
        'proceedings-article': 'paper-conference',
        'standard': 'entry',
        'reference-entry': 'entry',
    }

    def __init__(self, dictionary=None, **kwargs):
        """
        Can use both a dictionary or keywords to create a CSL_Item object:

            CSL_Item(title='The Book')
            CSL_Item({'title': 'The Book'})
            csl_dict = {'title': 'The Book', 'ISBN': '321-321'}
            CSL_Item(csl_dict, type='entry')
            CSL_Item(title='The Book', ISBN='321-321', type='entry')

        CSL_Item object is usually provided by bibliographic information API,
        but constructing small CSL_Item objects is useful for testing.
        """
        if dictionary is None:
            dictionary = dict()
        super().__init__(copy.deepcopy(dictionary))
        self.update(copy.deepcopy(kwargs))

    def set_id(self, id_):
        self['id'] = id_
        return self

    def correct_invalid_type(self):
        """
        Correct invalid CSL item type.
        Does nothing if `type` not present.

        For detail see https://github.com/CrossRef/rest-api-doc/issues/187
        """
        if 'type' in self:
            # Replace a type from in CSL_Item.type_mapping.keys(),
            # leave type intact in other cases.
            t = self['type']
            self['type'] = self.type_mapping.get(t, t)
        return self

    def set_default_type(self):
        """
        Set type to 'entry', if type not specified.
        """
        self['type'] = self.get('type', 'entry')
        return self

    def prune_against_schema(self):
        """
        Remove fields that violate the CSL Item JSON Schema.
        """
        from .citeproc import remove_jsonschema_errors
        csl_item, = remove_jsonschema_errors([self], in_place=True)
        assert csl_item is self
        return self

    def validate_against_schema(self):
        """
        Confirm that the CSL_Item validates. If not, raises a
        jsonschema.exceptions.ValidationError.
        """
        from .citeproc import get_jsonschema_csl_validator
        validator = get_jsonschema_csl_validator()
        validator.validate([self])
        return self

    def clean(self, prune: bool = True):
        """
        Sanitize and touch-up a potentially dirty CSL_Item.
        The following steps are performed:
        - update incorrect values for "type" field when a correct variant is known
        - remove fields that violate the JSON Schema (if prune=True)
        - set default value for "type" if missing, since CSL JSON requires type
        - validate against the CSL JSON schema (if prune=True) to ensure output
          CSL_Item is clean
        """
        logging.debug(
            f"Starting CSL_Item.clean with{'' if prune else 'out'}"
            f"CSL pruning for id: {self.get('id', 'id not specified')}")
        self.correct_invalid_type()
        if prune:
            self.prune_against_schema()
        self.set_default_type()
        if prune:
            self.validate_against_schema()
        return self

    @property
    def note(self) -> str:
        """
        Return the value of the "note" field as a string.
        If "note" key is not set, return empty string.
        """
        return str(self.get('note') or '')

    @note.setter
    def note(self, text: str):
        if text:
            self['note'] = text
        else:
            # if text is None or an empty string, remove the "note" field
            self.pop('note', None)

    @property
    def note_dict(self) -> dict:
        """
        Return a dictionary with key-value pairs encoded by this CSL Item's note.
        Extracts both forms (line-entry and braced-entry) of key-value pairs from the CSL JSON "cheater syntax"
        https://github.com/Juris-M/citeproc-js-docs/blob/93d7991d42b4a96b74b7281f38e168e365847e40/csl-json/markup.rst#cheater-syntax-for-odd-fields

        Assigning to this dict will not update `self["note"]`.
        """
        note = self.note
        line_matches = re.findall(
            r'^(?P<key>[A-Z]+|[-_a-z]+): *(?P<value>.+?) *$', note, re.MULTILINE)
        braced_matches = re.findall(
            r'{:(?P<key>[A-Z]+|[-_a-z]+): *(?P<value>.+?) *}', note)
        return dict(line_matches + braced_matches)

    def note_append_text(self, text: str):
        """
        Append text to the note field of a CSL Item.
        """
        if not text:
            return
        note = self.note
        if note and not note.endswith('\n'):
            note += '\n'
        note += text
        self.note = note

    def note_append_dict(self, dictionary: dict):
        """
        Append key-value pairs specified by `dictionary` to the note field of a CSL Item.
        Uses the the [CSL JSON "cheater syntax"](https://github.com/Juris-M/citeproc-js-docs/blob/93d7991d42b4a96b74b7281f38e168e365847e40/csl-json/markup.rst#cheater-syntax-for-odd-fields)
        to encode additional values not defined by the CSL JSON schema.
        """
        for key, value in dictionary.items():
            if not re.fullmatch(r'[A-Z]+|[-_a-z]+', key):
                logging.warning(
                    f'note_append_dict: skipping adding {key!r} because '
                    f'it does not conform to the variable_name syntax as per https://git.io/fjTzW.')
                continue
            if '\n' in value:
                logging.warning(
                    f'note_append_dict: skipping adding {key!r} because '
                    f'the value contains a newline: {value!r}')
                continue
            self.note_append_text(f'{key}: {value}')

    def infer_id(self):
        """
        Detect and set a non-null/empty for "id" or else raise a ValueError.
        """
        if self.get('standard_citation'):
            # "standard_citation" field is set with a non-null/empty value
            return self.set_id(self.pop('standard_citation'))
        if self.note_dict.get('standard_id'):
            # "standard_id" note field is set with a non-null/empty value
            return self.set_id(self.note_dict['standard_id'])
        if self.get('id'):
            # "id" field exists and is set with a non-null/empty value
            return self.set_id(infer_citekey_prefix(self['id']))
        raise ValueError(
            'infer_id could not detect a field with a citation / standard_citation. '
            'Consider setting the CSL Item "id" field.')

    def standardize_id(self):
        """
        Extract the standard_id (standard citation key) for a csl_item and modify the csl_item in-place to set its "id" field.
        The standard_id is extracted from a "standard_citation" field, the "note" field, or the "id" field.
        If extracting the citation from the "id" field, uses the infer_citekey_prefix function to set the prefix.
        For example, if the extracted standard_id does not begin with a supported prefix (e.g. "doi:", "pmid:" or "raw:"),
        the citation is assumed to be raw and given a "raw:" prefix.
        The extracted citation is checked for validity and standardized, after which it is the final "standard_id".

        Regarding csl_item modification, the csl_item "id" field is set to the standard_citation and the note field
        is created or updated with key-value pairs for standard_id and original_id.

        Note that the Manubot software generally refers to the "id" of a CSL Item as a citekey.
        However, in this context, we use "id" rather than "citekey" for consistency with CSL's "id" field.
        """
        original_id = self.get('id')
        self.infer_id()
        original_standard_id = self['id']
        assert is_valid_citekey(original_standard_id, allow_raw=True)
        standard_id = standardize_citekey(original_standard_id, warn_if_changed=False)
        add_to_note = {}
        note_dict = self.note_dict
        if original_id and original_id != standard_id:
            if original_id != note_dict.get('original_id'):
                add_to_note['original_id'] = original_id
        if original_standard_id and original_standard_id != standard_id:
            if original_standard_id != note_dict.get('original_standard_id'):
                add_to_note['original_standard_id'] = original_standard_id
        if standard_id != note_dict.get('standard_id'):
            add_to_note['standard_id'] = standard_id
        self.note_append_dict(dictionary=add_to_note)
        self.set_id(standard_id)
        return self


def assert_csl_item_type(x):
    if not isinstance(x, CSL_Item):
        raise TypeError(
            f'Expected CSL_Item object, got {type(x)}')
