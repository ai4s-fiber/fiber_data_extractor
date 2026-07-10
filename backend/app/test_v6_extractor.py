import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.paper import Paper
from app.services.legacy.v6_extractor import V6ExtractorService

async def test_extractor_logic():
    print("=== 开始运行 V6 Extractor 系统自动化质检与 Mapping 测试 ===")
    
    # 1. 模拟一个候选数据行
    mock_candidate = {
        "id": 1,
        "sample_id": "PI-ZIF-8-5%",
        "performance_metric": "tensile_strength",
        "performance_value": "9999",  # 故意设一个极大的拉伸强度测试极值质检
        "performance_unit": "MPa",
        "source_location": "Table 2"
    }

    # 2. 模拟一个包含对照样品的列表进行对照检查质检
    all_candidates_with_control = [
        mock_candidate,
        {
            "id": 2,
            "sample_id": "Pure PI (Control)", # 对照组
            "performance_metric": "tensile_strength",
            "performance_value": "72.4",
            "performance_unit": "MPa",
            "source_location": "Table 2"
        }
    ]

    print("\n[测试 1] 运行有对照样品的质检校验：")
    status, suggestions = V6ExtractorService.run_row_level_qc(mock_candidate, all_candidates_with_control)
    print(f"-> 质检自动评级: {status}")
    print(f"-> 诊断意见建议: {suggestions}")
    
    # 3. 模拟无对照样品的列表进行对照检查质检
    all_candidates_no_control = [
        mock_candidate
    ]
    
    print("\n[测试 2] 运行没有对照样品的质检校验：")
    status_nc, suggestions_nc = V6ExtractorService.run_row_level_qc(mock_candidate, all_candidates_no_control)
    print(f"-> 质检自动评级: {status_nc}")
    print(f"-> 诊断意见建议: {suggestions_nc}")

    # 4. 模拟一个极端异常指标单位测试
    bad_unit_candidate = {
        "id": 3,
        "sample_id": "PI-ZIF-8-5%",
        "performance_metric": "Youngs_modulus",
        "performance_value": "3.12",
        "performance_unit": "kg/m3",  # 模量的单位绝不可能是密度单位
        "source_location": "Figure 4"
    }
    print("\n[测试 3] 运行单位不合法指标的质检校验：")
    status_bad, suggestions_bad = V6ExtractorService.run_row_level_qc(bad_unit_candidate, [bad_unit_candidate])
    print(f"-> 质检自动评级: {status_bad}")
    print(f"-> 诊断意见建议: {suggestions_bad}")

    print("\n=== V6 Extractor 系统逻辑测试圆满完成！ ===")

if __name__ == "__main__":
    asyncio.run(test_extractor_logic())
