"""Data-source classification regression tests."""

from app.services.extractor_v7.data_source_classify import classify_data_source_type


def test_grounded_table_row_has_priority_over_nearby_reference_text():
    fact = {
        "fact_type": "performance",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 1,
        "assigned_sample_id": "acetylated jute",
        "metric_or_parameter": "oil_absorption_capacity",
        "value": "21.08",
        "unit": "g/g",
        "evidence_text": (
            "The values were greater than synthetic sorbents reported in literature [22,23].\n"
            "[columns]\tCycle\tOil sorbed (g/g)\n[row 1]\tFirst\t21.08"
        ),
    }

    assert classify_data_source_type(fact) == "paper_core_result"


def test_current_results_are_not_reclassified_by_literature_comparison_tail():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "assigned_sample_id": "FFRP laminate",
        "metric_or_parameter": "Youngs_modulus",
        "value": "14",
        "unit": "GPa",
        "evidence_text": (
            "The plot shows that stiffness increases from 12 to 14 GPa, "
            "in agreement with findings reported in the literature [30]."
        ),
    }

    assert classify_data_source_type(fact) == "paper_core_result"


def test_current_mean_values_survive_previous_work_comparison_context():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "assigned_sample_id": "PCL/AA/S",
        "metric_or_parameter": "water_contact_angle",
        "value": "93.4",
        "unit": "degree",
        "evidence_text": (
            "These results are shown as mean values of three measurements. "
            "In comparison with our previous work, the values differed. "
            "The contact angles were equal to 93.4 and 97.5 degrees for "
            "PCL/AA/S and PCL/AA/SBCu, respectively."
        ),
    }

    assert classify_data_source_type(fact) == "paper_core_result"


def test_explicit_external_report_remains_background():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "assigned_sample_id": "prior composite",
        "metric_or_parameter": "Youngs_modulus",
        "value": "14",
        "unit": "GPa",
        "evidence_text": "A modulus of 14 GPa was previously reported by Shah [30].",
    }

    assert classify_data_source_type(fact) == "background_reference"


def test_figure_grounding_survives_incorrect_intro_section_label():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "introduction",
        "assigned_sample_id": "PES_0.5G nanofiber membrane",
        "metric_or_parameter": "fiber_diameter",
        "value": "296",
        "unit": "nm",
        "source_location": "page 5, Fig. 2b",
        "evidence_text": (
            "When 0.5% graphene is added to PES, the diameter of nanofibers "
            "is 296 nm."
        ),
    }

    assert classify_data_source_type(fact) == "paper_core_result"


def test_known_bandgap_frequency_is_a_result_not_a_test_condition():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "assigned_sample_id": "fiber-reinforced TPMS",
        "metric_or_parameter": "bandgap frequency range",
        "value": "1050-1400",
        "unit": "Hz",
        "evidence_text": (
            "The fiber-reinforced TPMS has a directional bandgap from 1050 to 1400 Hz."
        ),
    }

    assert classify_data_source_type(fact) == "paper_core_result"
