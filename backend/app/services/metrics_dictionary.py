"""
Generic fiber material metrics dictionary — extensible, not paper-specific.

Used for:
1. Standardizing metric names (synonyms → canonical)
2. Prompting AI to not miss common metrics
3. Unit compatibility validation
4. Performance category classification

Unknown metrics are preserved as-is — the dictionary is a guide, not a filter.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Performance categories and their metrics
# ---------------------------------------------------------------------------

PERFORMANCE_CATEGORIES: dict[str, dict] = {
    "mechanical": {
        "label_zh": "力学性能",
        "metrics": {
            "tensile_strength": {
                "synonyms": ["拉伸强度", "breaking strength", "ultimate tensile strength",
                             "UTS",
                             "断裂强度", "抗拉强度", "sigma_r", "sigma_u",
                             "sigma r", "sigma u", "sigma_R (sigma_u)",
                             "σr", "σu", "σ_R", "σ_u"],
                "common_units": ["MPa", "GPa", "cN/dtex"],
            },
            "elongation_at_break": {
                "synonyms": ["断裂伸长率", "断裂伸长", "elongation", "strain at break",
                             "断裂延伸率", "extension at break", "epsilon_r", "epsilon_u",
                             "epsilon r", "epsilon u", "epsilon_R (epsilon_u)",
                             "varepsilon_r", "varepsilon_u", "varepsilon_r_varepsilon_u",
                             "\\varepsilon_R (\\varepsilon_u)",
                             "εr", "εu", "ε_R", "ε_u"],
                "common_units": ["%"],
            },
            "Youngs_modulus": {
                "synonyms": ["杨氏模量", "young's modulus", "youngs modulus", "elastic modulus",
                             "弹性模量", "tensile modulus", "E1", "E2", "E_1", "E_2",
                             "modulus E1", "modulus E2", "E2*", "Eγ*", "E_gamma_star",
                             "modulus E gamma star", "modulus_e_gamma_star",
                             "modulus_Egamma_star", "modulus_egamma_star",
                             "E_1_GPa", "E1_GPa", "e_1_gpa", "e1_gpa"],
                "common_units": ["MPa", "GPa"],
            },
            "Poissons_ratio": {
                "synonyms": ["Poisson's ratio", "Poisson ratio", "Poissons ratio", "泊松比"],
                "common_units": ["-", "dimensionless"],
            },
            "inelastic_threshold_stress": {
                "synonyms": [
                    "inelastic threshold stress", "inelastic strain threshold",
                    "threshold stress", "threshold load", "knee stress",
                ],
                "common_units": ["MPa", "GPa", "kPa"],
            },
            "knee_strain": {
                "synonyms": ["knee strain", "strain at knee", "knee-point strain"],
                "common_units": ["%"],
            },
            "damage_transition_strain": {
                "synonyms": ["damage transition strain", "damage-index transition strain"],
                "common_units": ["%"],
            },
            "stiffness_recovery_strain": {
                "synonyms": ["stiffness recovery strain", "strain at stiffness recovery"],
                "common_units": ["%"],
            },
            "compressive_strength": {
                "synonyms": ["压缩强度", "compression strength", "抗压强度"],
                "common_units": ["MPa", "GPa", "kPa"],
            },
            "compressive_stress": {
                "synonyms": ["压缩应力", "compression stress"],
                "common_units": ["MPa", "kPa"],
            },
            "cyclic_compression_stability": {
                "synonyms": ["循环压缩稳定性", "cyclic compression stability",
                             "compression cyclic stability", "fatigue resistance under compression"],
                "common_units": ["-", "MPa", "kPa"],
            },
            "flexural_strength": {
                "synonyms": ["弯曲强度", "flexural strength", "抗弯强度", "bending strength"],
                "common_units": ["MPa"],
            },
            "flexural_modulus": {
                "synonyms": ["弯曲模量", "flexural modulus", "bending modulus"],
                "common_units": ["MPa", "GPa"],
            },
            "hardness": {
                "synonyms": ["硬度", "shore hardness", "microhardness"],
                "common_units": ["-", "HV", "Shore D"],
            },
            "impact_strength": {
                "synonyms": ["冲击强度", "impact strength", "toughness"],
                "common_units": ["kJ/m²", "J/m"],
            },
            "tear_strength": {
                "synonyms": ["撕裂强度", "tear strength"],
                "common_units": ["MPa", "N/mm"],
            },
            "compressive_displacement": {
                "synonyms": [
                    "compressive displacement", "compression displacement",
                    "displacement deformation", "displacement at compressive load",
                    "displacement_at_compressive_load",
                ],
                "common_units": ["mm", "cm", "m"],
            },
            "softening_load": {
                "synonyms": ["softening load", "load at softening", "force at softening", "softening occurred"],
                "common_units": ["N", "kN"],
            },
            "load_bearing_stability_improvement": {
                "synonyms": ["load-bearing stability improvement", "load bearing stability increase"],
                "common_units": ["%"],
            },
            "bandgap_frequency_range": {
                "synonyms": ["bandgap frequency range", "directional bandgap", "frequency bandgap"],
                "common_units": ["Hz", "kHz", "MHz"],
            },
            "normalized_bandgap_frequency_range": {
                "synonyms": ["normalized bandgap frequency range", "normalized frequency range"],
                "common_units": ["-", "dimensionless"],
            },
            "eigenfrequency": {
                "synonyms": ["eigenfrequency", "eigen frequency", "natural frequency"],
                "common_units": ["Hz", "kHz", "MHz"],
            },
            "transmission_attenuation_frequency_range": {
                "synonyms": ["transmission attenuation frequency range", "transmission decay range", "transmission efficiency decay range"],
                "common_units": ["Hz", "kHz", "MHz"],
            },
            "maximum_acceleration": {
                "synonyms": ["maximum acceleration", "peak acceleration"],
                "common_units": ["-", "dimensionless", "m/s²"],
            },
            "acceleration_reduction": {
                "synonyms": ["acceleration reduction", "decrease in maximum acceleration"],
                "common_units": ["%"],
            },
            "specific_energy_absorption": {
                "synonyms": ["specific energy absorption", "SEA"],
                "common_units": ["J/kg", "kJ/kg", "J/g"],
            },
        },
    },
    "thermal": {
        "label_zh": "热性能",
        "metrics": {
            "thermal_conductivity": {
                "synonyms": ["热导率", "thermal conductivity", "导热系数"],
                "common_units": ["W/mK", "mW/mK"],
            },
            "surface_temperature": {
                "synonyms": ["表面温度", "surface temperature", "upper surface temperature",
                             "hot-stage surface temperature", "infrared surface temperature"],
                "common_units": ["°C", "C", "K"],
            },
            "thermal_diffusivity": {
                "synonyms": ["热扩散系数", "thermal diffusivity", "热扩散率"],
                "common_units": ["mm²/s", "m²/s"],
            },
            "glass_transition_temperature": {
                "synonyms": ["玻璃化转变温度", "Tg", "glass transition temperature"],
                "common_units": ["°C"],
            },
            "melting_temperature": {
                "synonyms": ["熔点", "Tm", "melting point", "melting temperature"],
                "common_units": ["°C"],
            },
            "crystallinity_Xc": {
                "synonyms": ["结晶度Xc", "crystallinity Xc", "Xc", "degree of crystallinity", "crystallinity"],
                "common_units": ["%"],
            },
            "beta_phase_crystallinity_Xbeta": {
                "synonyms": ["β相结晶度", "beta phase crystallinity", "Xbeta", "F(β)",
                             "beta crystallinity", "beta_phase_content_Fbeta"],
                "common_units": ["%"],
            },
            "decomposition_temperature": {
                "synonyms": ["分解温度", "Td", "decomposition temperature",
                             "thermal decomposition temperature", "Td5%", "Td10%",
                             "Td5", "Td10", "T_d5%", "T_d10%"],
                "common_units": ["°C"],
            },
            "weight_loss": {
                "synonyms": ["失重率", "weight loss", "mass loss", "TGA weight loss"],
                "common_units": ["%"],
            },
            "thermal_shrinkage": {
                "synonyms": ["热收缩率", "thermal shrinkage", "heat shrinkage", "收缩率"],
                "common_units": ["%"],
            },
            "coefficient_of_thermal_expansion": {
                "synonyms": ["热膨胀系数", "CTE", "coefficient of thermal expansion",
                             "thermal expansion coefficient"],
                "common_units": ["ppm/K", "10⁻⁶/K", "μm/m°C"],
            },
            "limiting_oxygen_index": {
                "synonyms": ["极限氧指数", "LOI", "limiting oxygen index", "氧指数"],
                "common_units": ["%"],
            },
            "UL94_rating": {
                "synonyms": ["UL-94", "UL94", "阻燃等级"],
                "common_units": ["-"],
            },
            "heat_release_rate": {
                "synonyms": ["热释放速率", "HRR", "heat release rate"],
                "common_units": ["kW/m²", "W/g"],
            },
            "peak_heat_release_rate": {
                "synonyms": ["峰值热释放速率", "PHRR", "peak HRR", "peak heat release rate"],
                "common_units": ["kW/m²", "W/g"],
            },
        },
    },
    "dielectric": {
        "label_zh": "介电性能",
        "metrics": {
            "dielectric_constant": {
                "synonyms": ["介电常数", "dielectric constant", "permittivity",
                             "相对介电常数", "relative permittivity", "εr", "ε_r", "Dk"],
                "common_units": ["-", "dimensionless"],
            },
            "dielectric_loss": {
                "synonyms": ["介电损耗", "dielectric loss", "imaginary permittivity",
                             "ε″", "epsilon double prime"],
                "common_units": ["-", "dimensionless"],
            },
            "loss_tangent": {
                "synonyms": ["损耗角正切", "loss tangent", "tan δ", "tan delta",
                             "Df", "dissipation factor", "tan d"],
                "common_units": ["-", "dimensionless"],
            },
            "breakdown_strength": {
                "synonyms": ["击穿强度", "breakdown strength", "dielectric strength",
                             "介电强度", "breakdown voltage"],
                "common_units": ["kV/mm", "V/μm", "MV/m"],
            },
            "electrical_conductivity": {
                "synonyms": ["电导率", "electrical conductivity", "electric conductivity",
                             "导电率", "conductivity"],
                "common_units": ["S/m", "S/cm", "mS/m"],
            },
            "volume_resistivity": {
                "synonyms": ["体积电阻率", "volume resistivity", "电阻率"],
                "common_units": ["Ω·m", "Ω·cm", "Ω/sq"],
            },
            "surface_resistivity": {
                "synonyms": ["表面电阻率", "surface resistivity", "表面电阻"],
                "common_units": ["Ω/sq", "Ω"],
            },
        },
    },
    "electromagnetic": {
        "label_zh": "电磁性能",
        "metrics": {
            "electromagnetic_interference_shielding_effectiveness": {
                "synonyms": ["电磁屏蔽效能", "EMI SE", "EMI shielding effectiveness",
                             "shielding effectiveness", "EMI shielding", "电磁屏蔽"],
                "common_units": ["dB"],
            },
            "electromagnetic_wave_transmittance": {
                "synonyms": ["电磁波透过率", "EM transmittance", "电磁透明度",
                             "electromagnetic transparency", "微波透过率"],
                "common_units": ["%", "dB"],
            },
            "electromagnetic_wave_reflectance": {
                "synonyms": ["电磁波反射率", "EM reflectance", "微波反射率"],
                "common_units": ["%"],
            },
            "electromagnetic_wave_absorptance": {
                "synonyms": ["电磁波吸收率", "EM absorptance", "微波吸收率"],
                "common_units": ["%"],
            },
        },
    },
    "piezoelectric": {
        "label_zh": "压电性能",
        "metrics": {
            "piezoelectric_coefficient_d33": {
                "synonyms": ["压电系数d33", "d33", "piezoelectric coefficient d33", "d₃₃"],
                "common_units": ["pC/N"],
            },
            "piezoelectric_coefficient_d31": {
                "synonyms": ["压电系数d31", "d31", "piezoelectric coefficient d31", "d₃₁"],
                "common_units": ["pC/N"],
            },
            "open_circuit_voltage": {
                "synonyms": ["开路电压", "open circuit voltage", "Voc", "V_oc", "输出电压"],
                "common_units": ["V", "mV"],
            },
            "short_circuit_current": {
                "synonyms": ["短路电流", "short circuit current", "Isc", "I_sc", "输出电流"],
                "common_units": ["μA", "nA", "mA"],
            },
            "output_power_density": {
                "synonyms": ["输出功率密度", "power density", "输出功率"],
                "common_units": ["μW/cm²", "mW/cm²", "nW/cm²"],
            },
            "piezoelectric_voltage_coefficient_g33": {
                "synonyms": ["压电电压系数", "g33", "g₃₃"],
                "common_units": ["V·m/N", "mV·m/N"],
            },
        },
    },
    "sensing": {
        "label_zh": "传感性能",
        "metrics": {
            "gauge_factor": {
                "synonyms": ["灵敏度系数", "gauge factor", "GF", "应变系数"],
                "common_units": ["-"],
            },
            "sensing_sensitivity": {
                "synonyms": ["传感灵敏度", "sensitivity", "灵敏度", "sensing sensitivity"],
                "common_units": ["kPa⁻¹", "%/strain", "V/kPa"],
            },
            "response_time": {
                "synonyms": ["响应时间", "response time", "反应时间"],
                "common_units": ["ms", "s"],
            },
            "recovery_time": {
                "synonyms": ["恢复时间", "recovery time", "回弹时间"],
                "common_units": ["ms", "s"],
            },
            "detection_limit": {
                "synonyms": ["检测限", "detection limit", "LOD", "最低检测限"],
                "common_units": ["Pa", "kPa", "ppm", "%"],
            },
            "working_range": {
                "synonyms": ["工作范围", "working range", "检测范围", "sensing range"],
                "common_units": ["%", "kPa", "Pa"],
            },
            "cyclic_stability": {
                "synonyms": ["循环稳定性", "cyclic stability", "durability", "耐久性",
                             "fatigue resistance", "cycling stability"],
                "common_units": ["cycles", "%"],
            },
            "sensitivity_low_pressure": {
                "synonyms": ["低压灵敏度", "low pressure sensitivity", "sensitivity at low pressure",
                             "low-pressure sensitivity"],
                "common_units": ["kPa⁻¹", "Pa⁻¹", "N⁻¹"],
            },
            "sensitivity_high_pressure": {
                "synonyms": ["高压灵敏度", "high pressure sensitivity", "sensitivity at high pressure",
                             "high-pressure sensitivity"],
                "common_units": ["kPa⁻¹", "Pa⁻¹", "N⁻¹"],
            },
            "loading_unloading_cycles": {
                "synonyms": ["加载卸载循环", "loading unloading cycles", "loading-unloading cycles",
                             "compression cycles", "pressure cycles"],
                "common_units": ["cycles"],
            },
            "linearity_R2": {
                "synonyms": ["线性度", "linearity", "R2", "R²", "coefficient of determination",
                             "linearity R2"],
                "common_units": ["-"],
            },
            "maximum_tested_force": {
                "synonyms": ["最大测试力", "maximum tested force", "max force", "maximum force"],
                "common_units": ["N", "kN"],
            },
            "detection_limit_force": {
                "synonyms": ["力检测限", "detection limit force", "minimum detectable force",
                             "force detection limit"],
                "common_units": ["N", "mN", "μN"],
            },
        },
    },
    "hydrophobicity": {
        "label_zh": "疏水/润湿性能",
        "metrics": {
            "water_contact_angle": {
                "synonyms": ["水接触角", "water contact angle", "WCA", "接触角",
                             "contact angle", "疏水角"],
                "common_units": ["°", "degree"],
            },
            "oil_contact_angle": {
                "synonyms": ["油接触角", "oil contact angle", "OCA", "疏油角"],
                "common_units": ["°", "degree"],
            },
            "water_absorption": {
                "synonyms": ["吸水率", "water absorption", "water uptake", "吸湿率"],
                "common_units": ["%"],
            },
            "oil_absorption_capacity": {
                "synonyms": [
                    "吸油量", "吸油容量", "oil absorption capacity",
                    "oil sorption capacity", "oil uptake capacity",
                    "oil absorbency", "oil sorption capacity per sorbent",
                    "oil_sorption_capacity", "oil sorbed", "oil_sorbed",
                ],
                "common_units": ["g/g", "g g⁻¹", "g/g sorbent"],
            },
            "oil_remaining_in_fiber": {
                "synonyms": [
                    "oil remaining in fiber", "residual oil in fiber",
                    "oil retained after squeezing", "remaining oil",
                ],
                "common_units": ["g/g", "g g⁻¹", "g/g sorbent"],
            },
            "moisture_regain": {
                "synonyms": ["回潮率", "moisture regain", "吸湿率"],
                "common_units": ["%"],
            },
        },
    },
    "physical": {
        "label_zh": "物理性能",
        "metrics": {
            "pH": {
                "synonyms": ["pH value", "solution pH", "medium pH"],
                "common_units": ["pH", "-", "dimensionless"],
            },
            "density": {
                "synonyms": ["密度", "density", "apparent density", "表观密度"],
                "common_units": ["g/cm³", "mg/cm³", "kg/m³"],
            },
            "porosity": {
                "synonyms": ["孔隙率", "porosity", "孔隙度"],
                "common_units": ["%"],
            },
            "specific_surface_area": {
                "synonyms": ["比表面积", "specific surface area", "BET surface area", "SSA"],
                "common_units": ["m²/g", "m²/g"],
            },
            "pore_volume": {
                "synonyms": ["孔体积", "pore volume", "孔容"],
                "common_units": ["cm³/g", "mL/g"],
            },
            "pore_size": {
                "synonyms": ["孔径", "pore size", "average pore diameter", "平均孔径"],
                "common_units": ["nm", "μm", "Å"],
            },
            "shrinkage": {
                "synonyms": ["收缩率", "shrinkage", "体积收缩", "volume shrinkage",
                             "linear shrinkage", "线收缩"],
                "common_units": ["%"],
            },
            "fiber_diameter": {
                "synonyms": ["纤维直径", "fiber diameter", "nanofiber diameter", "直径"],
                "common_units": ["nm", "μm", "mm"],
            },
            "fiber_length": {
                "synonyms": ["纤维长度", "fiber length", "nanofiber length", "长度"],
                "common_units": ["nm", "μm", "mm"],
            },
            "weight_percent_gain": {
                "synonyms": [
                    "增重率", "weight percent gain", "weight percentage gain", "WPG",
                ],
                "common_units": ["%"],
            },
            "degree_of_acetylation": {
                "synonyms": [
                    "乙酰化度", "degree of acetylation", "acetylation degree", "acetyl %",
                ],
                "common_units": ["%"],
            },
            "degree_of_substitution": {
                "synonyms": ["取代度", "degree of substitution", "DS"],
                "common_units": ["-", "dimensionless"],
            },
        },
    },
    "optical": {
        "label_zh": "光学性能",
        "metrics": {
            "transmittance": {
                "synonyms": ["透过率", "transmittance", "透光率", "transparency",
                             "optical transmittance", "光透过率"],
                "common_units": ["%"],
            },
            "reflectance": {
                "synonyms": ["反射率", "reflectance", "optical reflectance", "光反射率"],
                "common_units": ["%"],
            },
            "absorptance": {
                "synonyms": ["吸收率", "absorptance", "optical absorptance", "光吸收率"],
                "common_units": ["%"],
            },
            "refractive_index": {
                "synonyms": ["折射率", "refractive index", "RI"],
                "common_units": ["-"],
            },
        },
    },
    "electrochemical": {
        "label_zh": "电化学性能",
        "metrics": {
            "specific_capacitance": {
                "synonyms": ["比电容", "specific capacitance", "比容量", "capacitance"],
                "common_units": ["F/g", "mF/cm²", "F/cm³"],
            },
            "energy_density": {
                "synonyms": ["能量密度", "energy density"],
                "common_units": ["Wh/kg", "mWh/cm³", "Wh/L"],
            },
            "power_density": {
                "synonyms": ["功率密度", "power density"],
                "common_units": ["W/kg", "kW/kg", "mW/cm³"],
            },
            "coulombic_efficiency": {
                "synonyms": ["库伦效率", "coulombic efficiency", "CE", "库仑效率"],
                "common_units": ["%"],
            },
            "capacity_retention": {
                "synonyms": ["容量保持率", "capacity retention", "循环保持率"],
                "common_units": ["%"],
            },
        },
    },
    "filtration": {
        "label_zh": "过滤性能",
        "metrics": {
            "filtration_efficiency": {
                "synonyms": ["过滤效率", "filtration efficiency", "removal efficiency",
                             "collection efficiency"],
                "common_units": ["%"],
            },
            "pressure_drop": {
                "synonyms": ["压降", "pressure drop", "阻力压降", "filter pressure drop"],
                "common_units": ["Pa", "kPa", "mmH₂O"],
            },
            "quality_factor": {
                "synonyms": ["品质因子", "quality factor", "QF", "过滤品质因子"],
                "common_units": ["Pa⁻¹", "kPa⁻¹"],
            },
            "air_permeability": {
                "synonyms": ["透气率", "air permeability", "透气性", "gas permeability"],
                "common_units": ["mm/s", "L/m²/s", "mL/(cm²·s)"],
            },
        },
    },
    "stability": {
        "label_zh": "稳定性/耐久性",
        "metrics": {
            "thermal_stability": {
                "synonyms": ["热稳定性", "thermal stability", "heat resistance", "耐热性"],
                "common_units": ["°C"],
            },
            "chemical_stability": {
                "synonyms": ["化学稳定性", "chemical stability", "耐化学性"],
                "common_units": ["-"],
            },
            "UV_resistance": {
                "synonyms": ["抗紫外", "UV resistance", "紫外稳定性", "耐紫外"],
                "common_units": ["%", "h"],
            },
            "oxidation_resistance": {
                "synonyms": ["抗氧化性", "oxidation resistance", "抗氧化"],
                "common_units": ["-", "°C"],
            },
            "stress_retention": {
                "synonyms": ["应力保持率", "stress retention", "stress retention rate"],
                "common_units": ["%"],
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Structure features (characterization results)
# ---------------------------------------------------------------------------

STRUCTURE_FEATURES: dict[str, dict] = {
    "fiber_diameter": {
        "synonyms": ["纤维直径", "fiber diameter", "nanofiber diameter", "平均直径"],
        "common_units": ["nm", "μm", "mm"],
    },
    "fiber_length": {
        "synonyms": ["纤维长度", "fiber length"],
        "common_units": ["μm", "mm", "cm"],
    },
    "linear_density": {
        "synonyms": ["线密度", "linear density", "fineness", "细度"],
        "common_units": ["dtex", "tex", "denier"],
    },
    "crystallinity": {
        "synonyms": ["结晶度", "crystallinity", "Xc", "degree of crystallinity"],
        "common_units": ["%"],
    },
    "orientation_factor": {
        "synonyms": ["取向因子", "orientation factor", "取向度", "degree of orientation",
                     "Herman's factor", "fc", "fa"],
        "common_units": ["-"],
    },
    "fiber_volume_fraction": {
        "synonyms": ["fiber volume fraction", "fibre volume fraction", "fiber content", "fibre content"],
        "common_units": ["%", "vol%"],
    },
    "beta_phase_content": {
        "synonyms": ["β相含量", "beta phase content", "β-phase content", "F(β)",
                     "beta phase fraction"],
        "common_units": ["%"],
    },
    "phase_structure": {
        "synonyms": ["相结构", "phase structure", "晶型", "crystal form",
                     "crystalline phase", "晶相"],
        "common_units": ["-"],
    },
    "pore_size": {
        "synonyms": ["孔径", "pore size", "pore diameter"],
        "common_units": ["nm", "μm", "Å"],
    },
    "cross_section_shape": {
        "synonyms": ["截面形状", "cross section", "cross-section", "截面形态"],
        "common_units": ["-"],
    },
    "surface_roughness": {
        "synonyms": ["表面粗糙度", "surface roughness", "Ra", "RMS roughness"],
        "common_units": ["nm", "μm"],
    },
    "specific_surface_area": {
        "synonyms": ["比表面积", "specific surface area", "BET", "SSA"],
        "common_units": ["m²/g"],
    },
    "free_volume_fraction": {
        "synonyms": ["自由体积分数", "free volume fraction", "FFV"],
        "common_units": ["%", "-"],
    },
    "imidization_degree": {
        "synonyms": ["酰亚胺化程度", "imidization degree", "degree of imidization",
                     "亚胺化程度", "ID"],
        "common_units": ["%"],
    },
}

# ---------------------------------------------------------------------------
# Process parameters
# ---------------------------------------------------------------------------

PROCESS_PARAMETERS: dict[str, dict] = {
    "polymer_concentration": {
        "synonyms": ["聚合物浓度", "polymer concentration", "solution concentration",
                     "纺丝液浓度"],
        "common_units": ["wt%", "w/v%", "g/mL"],
    },
    "spinning_temperature": {
        "synonyms": ["纺丝温度", "spinning temperature", "extrusion temperature",
                     "挤出温度", "melt temperature"],
        "common_units": ["°C"],
    },
    "voltage": {
        "synonyms": ["电压", "voltage", "applied voltage", "纺丝电压", "静电纺丝电压"],
        "common_units": ["kV", "V"],
    },
    "electric_field_strength": {
        "synonyms": ["电场强度", "electric field strength", "electric field intensity",
                     "average field intensity", "field intensity"],
        "common_units": ["kV/cm", "V/m", "kV/mm"],
    },
    "tip_to_collector_distance": {
        "synonyms": ["接收距离", "tip-to-collector distance", "working distance",
                     "纺丝距离", "collector distance", "针头到接收器距离",
                     "distance from needle to collector", "needle to collector distance"],
        "common_units": ["cm", "mm"],
    },
    "flow_rate": {
        "synonyms": ["流速", "flow rate", "feed rate", "进料速率", "供液速率",
                     "推注速度", "injection rate", "flowrate"],
        "common_units": ["mL/h", "μL/min", "mL/min"],
    },
    "total_flow_rate": {
        "synonyms": ["总流量", "total flow rate", "total flowrate", "total feed rate"],
        "common_units": ["mL/h", "μL/min", "mL/min"],
    },
    "flow_rate_per_needle": {
        "synonyms": ["单针流量", "flow rate per needle", "flowrate per needle",
                     "per-needle flow rate", "natural flow rate per needle"],
        "common_units": ["mL/h", "μL/min", "mL/min"],
    },
    "spinning_time": {
        "synonyms": ["纺丝时间", "spinning time", "electrospinning time", "ES time"],
        "common_units": ["h", "min", "s"],
    },
    "needle_gauge": {
        "synonyms": ["针规", "needle gauge", "needle size", "needle gauge size"],
        "common_units": ["G", "mm"],
    },
    "number_of_needles": {
        "synonyms": ["针数", "number of needles", "no. of needles", "needle count"],
        "common_units": ["-"],
    },
    "needle_spacing": {
        "synonyms": ["针间距", "needle spacing", "distance between needles",
                     "needle-to-needle distance", "needle center-to-center distance"],
        "common_units": ["mm", "cm"],
    },
    "take_up_speed": {
        "synonyms": ["卷绕速度", "take-up speed", "take up speed", "winding speed",
                     "collector rotation speed", "接收转速"],
        "common_units": ["m/min", "rpm", "mm/s"],
    },
    "draw_ratio": {
        "synonyms": ["牵伸倍数", "draw ratio", "stretching ratio", "拉伸倍数",
                     "draft ratio", "牵伸比"],
        "common_units": ["×", "-"],
    },
    "drawing_temperature": {
        "synonyms": ["牵伸温度", "drawing temperature", "拉伸温度", "热牵伸温度"],
        "common_units": ["°C"],
    },
    "coagulation_bath": {
        "synonyms": ["凝固浴", "coagulation bath", "凝固浴组成"],
        "common_units": ["-"],
    },
    "coagulation_temperature": {
        "synonyms": ["凝固浴温度", "coagulation temperature", "凝固温度"],
        "common_units": ["°C"],
    },
    "drying_temperature": {
        "synonyms": ["干燥温度", "drying temperature", "烘干温度"],
        "common_units": ["°C"],
    },
    "drying_time": {
        "synonyms": ["干燥时间", "drying time", "烘干时间"],
        "common_units": ["h", "min", "s"],
    },
    "annealing_temperature": {
        "synonyms": ["退火温度", "annealing temperature", "热处理温度",
                     "heat treatment temperature"],
        "common_units": ["°C"],
    },
    "annealing_time": {
        "synonyms": ["退火时间", "annealing time", "热处理时间", "heat treatment time"],
        "common_units": ["h", "min", "s"],
    },
    "carbonization_temperature": {
        "synonyms": ["碳化温度", "carbonization temperature"],
        "common_units": ["°C"],
    },
    "carbonization_time": {
        "synonyms": ["碳化时间", "carbonization time"],
        "common_units": ["h", "min"],
    },
    "stabilization_temperature": {
        "synonyms": ["预氧化温度", "stabilization temperature", "稳定化温度"],
        "common_units": ["°C"],
    },
    "pressure": {
        "synonyms": ["压力", "pressure", "applied pressure", "均质压力"],
        "common_units": ["bar", "MPa", "kPa", "psi"],
    },
    "atmosphere": {
        "synonyms": ["气氛", "atmosphere", "gas atmosphere", "保护气氛"],
        "common_units": ["-"],
    },
    "humidity": {
        "synonyms": ["湿度", "humidity", "relative humidity", "RH", "相对湿度"],
        "common_units": ["%"],
    },
}

# ---------------------------------------------------------------------------
# Metric priority for export and review
# ---------------------------------------------------------------------------

CORE_METRICS: set[str] = {
    "density",
    "porosity",
    "shrinkage",
    "thermal_shrinkage",
    "fiber_diameter",
    "fiber_length",
    "thermal_conductivity",
    "surface_temperature",
    "tensile_strength",
    "elongation_at_break",
    "Youngs_modulus",
    "inelastic_threshold_stress",
    "compressive_strength",
    "compressive_stress",
    "flexural_strength",
    "flexural_modulus",
    "water_contact_angle",
    "pH",
    "oil_contact_angle",
    "dielectric_constant",
    "dielectric_loss",
    "loss_tangent",
    "electrical_conductivity",
    "breakdown_strength",
    "piezoelectric_coefficient_d33",
    "piezoelectric_coefficient_d31",
    "open_circuit_voltage",
    "short_circuit_current",
    "output_power_density",
    "gauge_factor",
    "sensing_sensitivity",
    "response_time",
    "recovery_time",
    "cyclic_stability",
    "cyclic_compression_stability",
    "electromagnetic_wave_transmittance",
    "filtration_efficiency",
    "pressure_drop",
    "air_permeability",
    "specific_capacitance",
    "energy_density",
    "power_density",
    "capacity_retention",
}

SECONDARY_METRICS: set[str] = {
    "specific_surface_area",
    "pore_volume",
    "pore_size",
    "crystallinity",
    "free_volume_fraction",
    "imidization_degree",
    "thermal_stability",
    "glass_transition_temperature",
    "melting_temperature",
    "decomposition_temperature",
    "coefficient_of_thermal_expansion",
    "limiting_oxygen_index",
}

SECONDARY_KEYWORDS: tuple[str, ...] = (
    "xps", "ftir", "raman", "xrd", "binding energy", "peak", "peak position",
    "imidization degree", "imidization time", "reaction pathway",
    "reaction fraction", "reaction ratio", "reaction conversion",
    "fractional free volume", "free volume", "ffv", "simulation",
    "calculation", "activation energy", "molecular dynamics", "pathway",
    "measurement temperature", "measuring temperature", "test temperature",
    "testing temperature", "measurement frequency", "test frequency",
    "measurement pressure", "test pressure", "measurement humidity",
    "test humidity", "frequency range", "pressure range",
)

NARRATIVE_KEYWORDS: tuple[str, ...] = (
    "excellent", "superior", "good", "poor", "highly", "remarkable",
    "outstanding", "enhanced", "improved", "qualitative",
)

CONDITION_PARAMETER_NAMES: tuple[str, ...] = (
    "measurement temperature",
    "measuring temperature",
    "test temperature",
    "testing temperature",
    "experimental temperature",
    "measurement frequency",
    "measuring frequency",
    "test frequency",
    "testing frequency",
    "measurement pressure",
    "test pressure",
    "pressure range",
    "measurement humidity",
    "test humidity",
    "measurement time",
    "test time",
    "measurement frequency range",
    "test frequency range",
    "frequency range",
    "simulation temperature",
    "simulation pressure",
    "simulation time",
)

CONDITION_PARAMETER_QUALIFIERS: tuple[str, ...] = (
    "measurement", "measuring", "test", "testing", "experimental",
    "ambient", "environmental", "operating", "simulation", "simulated",
)

CONDITION_PARAMETER_TARGETS: tuple[str, ...] = (
    "temperature", "frequency", "pressure", "humidity", "time", "duration",
)

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def find_metric_canonical(name: str) -> str | None:
    """Given a metric name (possibly a synonym), return the canonical name."""
    lower = name.strip().lower()
    phrase_candidates: list[tuple[str, str]] = []
    # Pass 1: exact match on canonical name or synonym
    for cat in PERFORMANCE_CATEGORIES.values():
        for canonical, info in cat["metrics"].items():
            if lower == canonical.lower():
                return canonical
            phrase_candidates.append((canonical.lower().replace("_", " "), canonical))
            for syn in info["synonyms"]:
                if syn.lower() == lower:
                    return canonical
                phrase_candidates.append((syn.lower(), canonical))
    if is_condition_parameter_name(lower):
        return None
    # Substring lookup is unsafe for short symbols: "E" previously matched
    # the first metric containing that letter. Short scientific symbols must
    # be registered as exact synonyms or resolved with table context.
    if len(re.sub(r"[^a-z0-9]", "", lower)) < 3:
        return None
    # Pass 2: substring match — require canonical/synonym to CONTAIN the input
    # (avoids "density" matching "power density" → "output_power_density")
    for cat in PERFORMANCE_CATEGORIES.values():
        for canonical, info in cat["metrics"].items():
            if lower in canonical.lower():
                return canonical
            for syn in info["synonyms"]:
                if lower in syn.lower():
                    return canonical
    # Pass 3: input contains a longer canonical/synonym phrase with extra condition text.
    # Sort longest first so "thermal conductivity" wins before generic "conductivity".
    ambiguous_short_terms = {"density", "conductivity", "temperature", "strength", "modulus", "sensitivity"}
    for phrase, canonical in sorted(phrase_candidates, key=lambda item: len(item[0]), reverse=True):
        phrase = phrase.strip()
        if not phrase or phrase in ambiguous_short_terms:
            continue
        if len(phrase) < 5:
            continue
        if re_search_word_phrase(phrase, lower):
            return canonical
    return None


def re_search_word_phrase(phrase: str, text: str) -> bool:
    """Match a phrase inside text with loose separators but word boundaries."""
    escaped = re.escape(phrase).replace(r"\ ", r"[\s_\-/]+")
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text))

def find_category_for_metric(metric: str) -> str:
    """Return the performance_category for a given metric name."""
    canonical = find_metric_canonical(metric) or metric
    lower = canonical.lower()
    for cat_name, cat_info in PERFORMANCE_CATEGORIES.items():
        for m_name in cat_info["metrics"]:
            if m_name.lower() == lower:
                return cat_name
    return "physical"

def classify_metric_priority(metric: str) -> str:
    """Classify a metric for review/export priority.

    Returns one of: Core, Secondary, Narrative.
    The rules are generic across papers: dictionary-known performance metrics are
    core unless they are characterization/process descriptors; spectroscopy,
    reaction pathway, and simulation values are secondary.
    """
    raw = (metric or "").strip()
    lower = raw.lower()
    canonical = find_metric_canonical(raw) or find_structure_feature_canonical(raw) or raw
    if canonical == raw and is_condition_parameter_name(raw):
        return "Secondary"

    if any(keyword in lower for keyword in NARRATIVE_KEYWORDS):
        return "Narrative"
    if canonical in CORE_METRICS:
        return "Core"
    if canonical in SECONDARY_METRICS:
        return "Secondary"
    if any(keyword in lower for keyword in SECONDARY_KEYWORDS):
        return "Secondary"

    for cat_info in PERFORMANCE_CATEGORIES.values():
        if canonical in cat_info["metrics"]:
            return "Core"

    if canonical in STRUCTURE_FEATURES:
        return "Secondary"
    return "Secondary"

def is_condition_parameter_name(metric: str) -> bool:
    """Return True when a metric-like name is actually a measurement condition.

    Examples: "thermal conductivity measurement temperature" and
    "dielectric constant test frequency" should not be canonicalized into the
    measured property itself.
    """
    lower = (metric or "").strip().lower().replace("_", " ")
    if not lower:
        return False
    # Exact registered performance metrics win over generic condition phrases.
    # For example, a bandgap_frequency_range is a result, not a test frequency.
    for category in PERFORMANCE_CATEGORIES.values():
        for canonical, info in category["metrics"].items():
            if lower == canonical.lower().replace("_", " "):
                return False
            if any(lower == synonym.lower().replace("_", " ") for synonym in info["synonyms"]):
                return False
    if any(name in lower for name in CONDITION_PARAMETER_NAMES):
        return True
    return (
        any(qualifier in lower for qualifier in CONDITION_PARAMETER_QUALIFIERS)
        and any(target in lower for target in CONDITION_PARAMETER_TARGETS)
    )

def get_common_units(metric: str) -> list[str]:
    """Return common units for a metric name."""
    canonical = find_metric_canonical(metric) or metric
    lower = canonical.lower()
    for cat in PERFORMANCE_CATEGORIES.values():
        for m_name, info in cat["metrics"].items():
            if m_name.lower() == lower:
                return info["common_units"]
    return []

def find_structure_feature_canonical(name: str) -> str | None:
    """Given a structure feature name, return the canonical name."""
    lower = name.strip().lower()
    for canonical, info in STRUCTURE_FEATURES.items():
        if lower == canonical.lower():
            return canonical
        for syn in info["synonyms"]:
            if syn.lower() == lower or syn.lower() in lower or lower in syn.lower():
                return canonical
    return None

def find_process_parameter_canonical(name: str) -> str | None:
    """Return a process canonical name without drifting into performance metrics."""
    lower = (name or "").strip().lower().replace("_", " ")
    if not lower:
        return None

    candidates: list[tuple[str, str]] = []
    for canonical, info in PROCESS_PARAMETERS.items():
        phrases = [canonical.replace("_", " "), *(info.get("synonyms") or [])]
        for phrase in phrases:
            normalized = str(phrase).strip().lower().replace("_", " ")
            if not normalized:
                continue
            if lower == normalized:
                return canonical
            candidates.append((normalized, canonical))

    for phrase, canonical in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if len(phrase) >= 4 and re_search_word_phrase(phrase, lower):
            return canonical
    return None

def all_metric_names() -> list[str]:
    """Return all canonical metric names across all categories."""
    names = []
    for cat in PERFORMANCE_CATEGORIES.values():
        names.extend(cat["metrics"].keys())
    return names

def all_structure_feature_names() -> list[str]:
    """Return all canonical structure feature names."""
    return list(STRUCTURE_FEATURES.keys())

def all_process_parameter_names() -> list[str]:
    """Return all canonical process parameter names."""
    return list(PROCESS_PARAMETERS.keys())

def build_metrics_prompt_text() -> str:
    """Build a prompt string listing all metric categories for the LLM."""
    lines = []
    for cat_name, cat_info in PERFORMANCE_CATEGORIES.items():
        label = cat_info["label_zh"]
        metrics = ", ".join(cat_info["metrics"].keys())
        lines.append(f"  {label} ({cat_name}): {metrics}")
    return "\n".join(lines)

def build_structure_prompt_text() -> str:
    """Build a prompt string listing all structure features for the LLM."""
    features = ", ".join(STRUCTURE_FEATURES.keys())
    return features

def build_process_prompt_text() -> str:
    """Build a prompt string listing all process parameters for the LLM."""
    params = ", ".join(PROCESS_PARAMETERS.keys())
    return params
