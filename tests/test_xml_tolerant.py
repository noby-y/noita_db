"""The reader has to survive everything Noita's own loader accepts."""

import pytest

from noita_db.xml_tolerant import XmlError, parse_string


def test_duplicate_attributes_are_all_kept():
    element = parse_string('<C mass="2" mass="3" mass="4" />')
    assert element.attrs == [('mass', '2'), ('mass', '3'), ('mass', '4')]
    assert element.get('mass') == '2'
    assert list(element.iter_attrs('mass')) == ['2', '3', '4']


def test_comment_between_attributes():
    element = parse_string('<C a="1" <!-- why not --> b="2" />')
    assert element.attrs == [('a', '1'), ('b', '2')]


def test_comment_containing_dashes():
    element = parse_string('<E><!-- art - main body ------- --><C a="1"/></E>')
    assert [c.tag for c in element.children] == ['C']


def test_unclosed_root_is_closed_at_eof():
    element = parse_string('<Entity><C a="1"></C>\n<Entity>')
    assert element.tag == 'Entity'
    assert [c.tag for c in element.children] == ['C', 'Entity']


def test_mismatched_closing_tag_is_dropped():
    element = parse_string('<E><A/></B></E>')
    assert [c.tag for c in element.children] == ['A']


def test_nested_children_and_self_closing():
    element = parse_string('<P><C><D x="1"/></C></P>')
    assert element.children[0].children[0].attrs == [('x', '1')]


def test_valueless_attribute():
    assert parse_string('<C flag b="2"/>').attrs == [('flag', ''), ('b', '2')]


def test_line_numbers_are_reported():
    element = parse_string('<E>\n\n  <C a="1"/>\n</E>')
    assert element.children[0].line == 3


def test_empty_input_raises():
    with pytest.raises(XmlError):
        parse_string('   \n<!-- nothing here -->\n')
