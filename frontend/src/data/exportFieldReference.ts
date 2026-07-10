/**
 * Field descriptions for each sheet in the structured export workbook.
 * Keep column order aligned with backend `workbook_export.py`.
 */

export interface FieldRef {
  no: number;
  en: string;
  zh: string;
  meaning: string;
}

export interface ExportSheetRef {
  key: string;
  title: string;
  subtitle: string;
  description: string;
  fields: FieldRef[];
}

/** Main_Data column names only — used by backend column consistency test. */
export const MAIN_DATA_COLUMN_NAMES = [
  'record_id',
  'paper_id',
  'sample_id',
  'sample_group_id',
  'material_system',
  'fiber_type',
  'variable_name',
  'variable_value',
  'variable_unit',
  'composition_expression',
  'matrix_name',
  'matrix_content',
  'matrix_unit',
  'additive_expression',
  'solvent_or_aid',
  'process_route',
  'spinning_method',
  'process_parameters',
  'post_treatment',
  'structure_methods',
  'structure_features',
  'performance_category',
  'performance_metric',
  'performance_value',
  'performance_unit',
  'performance_method',
  'performance_condition',
  'evidence_id',
  'source_page',
  'confidence',
  'review_status',
  'reviewer_comment',
] as const;

export const EXPORT_SHEET_REFERENCES: ExportSheetRef[] = [
  {
    key: 'Main_Data',
    title: 'Main_Data',
    subtitle: '主数据表（32 列）',
    description:
      '每一行是一条完整的材料数据记录，包含样品、成分、工艺、结构与性能。文献元数据与完整证据文本在其它 Sheet 中，不在此重复。',
    fields: [
      { no: 1, en: 'record_id', zh: '数据记录编号', meaning: '每一行数据的唯一编号' },
      { no: 2, en: 'paper_id', zh: '文献编号', meaning: '指向 Papers 表的文献编号，如 P0001' },
      { no: 3, en: 'sample_id', zh: '样品编号', meaning: '文献中给出的样品名，如 PET-3、S1、PVDF-BT-1.5' },
      { no: 4, en: 'sample_group_id', zh: '样品组编号', meaning: '同一组变量实验的编号' },
      { no: 5, en: 'material_system', zh: '材料体系', meaning: '材料组成体系，如 PET/TiO₂、PVDF/BaTiO₃、PAN/CNT' },
      { no: 6, en: 'fiber_type', zh: '纤维类型', meaning: '熔融纺丝长丝、湿法纺丝纤维、电纺纳米纤维、碳纤维等' },
      { no: 7, en: 'variable_name', zh: '变量名称', meaning: '该组样品主要变化的因素' },
      { no: 8, en: 'variable_value', zh: '变量数值', meaning: '当前样品对应的变量值' },
      { no: 9, en: 'variable_unit', zh: '变量单位', meaning: '变量单位，如 wt%、℃、×、min、m/min' },
      { no: 10, en: 'composition_expression', zh: '成分表达式', meaning: '完整成分配方' },
      { no: 11, en: 'matrix_name', zh: '基体/前驱体名称', meaning: '主体材料名称，如 PET、PVDF、PAN、PA6' },
      { no: 12, en: 'matrix_content', zh: '基体/前驱体含量', meaning: '主体材料含量' },
      { no: 13, en: 'matrix_unit', zh: '基体/前驱体单位', meaning: '主体材料含量单位，如 wt%、vol%、mol%' },
      { no: 14, en: 'additive_expression', zh: '填料/改性组分表达式', meaning: '填料、增强相、功能组分信息' },
      { no: 15, en: 'solvent_or_aid', zh: '溶剂/助剂', meaning: '溶剂、分散剂、相容剂、加工助剂等' },
      { no: 16, en: 'process_route', zh: '工艺路线', meaning: '总体制备路线' },
      { no: 17, en: 'spinning_method', zh: '纺丝方法', meaning: '具体纺丝方法，如 melt spinning、wet spinning、electrospinning' },
      { no: 18, en: 'process_parameters', zh: '工艺参数', meaning: '关键工艺参数' },
      { no: 19, en: 'post_treatment', zh: '后处理条件', meaning: '退火、热牵伸、碳化、稳定化、洗涤、干燥等后处理' },
      { no: 20, en: 'structure_methods', zh: '结构表征方法', meaning: 'SEM、XRD、DSC、FTIR、Raman、WAXS、SAXS 等' },
      { no: 21, en: 'structure_features', zh: '结构特征', meaning: '结构指标集合，如 crystallinity=36.5%; fiber_diameter=18.5 μm' },
      { no: 22, en: 'performance_category', zh: '性能类别', meaning: '力学性能、热性能、电学性能、压电性能、传感性能等' },
      { no: 23, en: 'performance_metric', zh: '性能指标', meaning: '单一性能指标名称，如 tensile_strength、elongation_at_break' },
      { no: 24, en: 'performance_value', zh: '性能数值', meaning: '性能指标对应的数值' },
      { no: 25, en: 'performance_unit', zh: '性能单位', meaning: '性能单位，如 MPa、%、GPa、S/m、pC/N、V' },
      { no: 26, en: 'performance_method', zh: '性能测试方法', meaning: '测试方法或标准' },
      { no: 27, en: 'performance_condition', zh: '性能测试条件', meaning: '测试条件，如 gauge_length=20 mm; tensile_speed=10 mm/min' },
      { no: 28, en: 'evidence_id', zh: '证据编号', meaning: '指向 Evidence 表的证据行 ID' },
      { no: 29, en: 'source_page', zh: '来源页码', meaning: '支撑该记录的主要证据页码' },
      { no: 30, en: 'confidence', zh: '置信度', meaning: '该条记录的抽取可信度，通常为 0–1' },
      { no: 31, en: 'review_status', zh: '审核状态', meaning: '待审核、已修改、通过、存疑、缺失、已删除' },
      { no: 32, en: 'reviewer_comment', zh: '审核意见', meaning: '学生或审核人对该条数据的备注' },
    ],
  },
  {
    key: 'Papers',
    title: 'Papers',
    subtitle: '文献元数据表（11 列）',
    description: '每篇文献一行，存放标题、DOI、期刊等元数据，供 Main_Data 通过 paper_id 关联引用。',
    fields: [
      { no: 1, en: 'paper_id', zh: '文献编号', meaning: '导出用文献编号，与 Main_Data.paper_id 一致' },
      { no: 2, en: 'source_paper_db_id', zh: '系统文献 ID', meaning: '平台内部文献记录的数据库主键' },
      { no: 3, en: 'original_filename', zh: '原始文件名', meaning: '上传的 PDF 原始文件名' },
      { no: 4, en: 'paper_title', zh: '文献标题', meaning: '论文标题' },
      { no: 5, en: 'doi_or_url', zh: 'DOI/链接', meaning: 'DOI 或可访问的文献 URL' },
      { no: 6, en: 'year', zh: '发表年份', meaning: '文献发表年份' },
      { no: 7, en: 'journal', zh: '期刊', meaning: '发表期刊名称' },
      { no: 8, en: 'authors', zh: '作者', meaning: '作者列表（预留字段）' },
      { no: 9, en: 'publisher', zh: '出版方', meaning: '出版社或出版机构（预留字段）' },
      { no: 10, en: 'abstract', zh: '摘要', meaning: '文献摘要（预留字段）' },
      { no: 11, en: 'supplementary_url', zh: '补充材料链接', meaning: 'Supporting Information 等补充材料 URL（预留字段）' },
    ],
  },
  {
    key: 'Evidence',
    title: 'Evidence',
    subtitle: '证据明细表（12 列）',
    description:
      '每条证据对应文献中的一个文本/表格/图注片段，含完整 evidence_text。Main_Data 通过 evidence_id 关联到此表。',
    fields: [
      { no: 1, en: 'evidence_id', zh: '证据编号', meaning: '证据行的唯一 ID，被 Main_Data.evidence_id 引用' },
      { no: 2, en: 'paper_id', zh: '文献编号', meaning: '所属文献的 paper_id' },
      { no: 3, en: 'record_id', zh: '数据记录编号', meaning: '关联的 Main_Data 记录编号' },
      { no: 4, en: 'sample_id', zh: '样品编号', meaning: '关联样品的编号' },
      { no: 5, en: 'block_id', zh: '文档块 ID', meaning: '指向 Parse_Blocks 中的 MinerU 文档块' },
      { no: 6, en: 'page_number', zh: '页码', meaning: '证据所在 PDF 页码' },
      { no: 7, en: 'bbox', zh: '版面坐标', meaning: '证据在页面上的边界框 JSON（x0,y0,x1,y1）' },
      { no: 8, en: 'source_type', zh: '来源类型', meaning: 'text、table、figure_caption 等来源分类' },
      { no: 9, en: 'mineru_block_type', zh: 'MinerU 块类型', meaning: 'MinerU 解析得到的块类型' },
      { no: 10, en: 'source_location', zh: '来源位置描述', meaning: '如 Fig. 3、Table 2、Section 3.2 等可读位置' },
      { no: 11, en: 'evidence_text', zh: '证据原文', meaning: '支撑该数据点的完整原文摘录' },
      { no: 12, en: 'confidence', zh: '置信度', meaning: '该条证据的抽取可信度' },
    ],
  },
  {
    key: 'Parse_Blocks',
    title: 'Parse_Blocks',
    subtitle: '文档解析块表（9 列）',
    description:
      'MinerU 解析后的文档块清单，用于溯源与调试。默认随导出附带，便于审核人员核对原文版面结构。',
    fields: [
      { no: 1, en: 'block_id', zh: '文档块 ID', meaning: 'MinerU 文档块的唯一标识' },
      { no: 2, en: 'paper_id', zh: '文献编号', meaning: '所属文献的 paper_id' },
      { no: 3, en: 'page_number', zh: '页码', meaning: '块所在页码' },
      { no: 4, en: 'order_index', zh: '阅读顺序', meaning: '该页内块的排列顺序' },
      { no: 5, en: 'block_type', zh: '块类型', meaning: 'title、text、table、figure 等类型' },
      { no: 6, en: 'section_name', zh: '章节名', meaning: '所属章节或标题上下文' },
      { no: 7, en: 'bbox', zh: '版面坐标', meaning: '块在页面上的边界框 JSON' },
      { no: 8, en: 'text_preview', zh: '文本预览', meaning: '块内文本前 500 字符预览' },
      { no: 9, en: 'related_block_ids', zh: '关联块 ID', meaning: '与当前块相关的其它 block_id 列表 JSON' },
    ],
  },
  {
    key: 'Quality_Report',
    title: 'Quality_Report',
    subtitle: '质量报告表（2 列）',
    description:
      '导出数据的质量统计摘要。每行一个指标（metric）及其数值（value），如主数据行数、证据覆盖率、存疑行数等。',
    fields: [
      { no: 1, en: 'metric', zh: '质量指标', meaning: '指标名称，如 main_data_rows、evidence_rows、approved_rows、uncertain_rows' },
      { no: 2, en: 'value', zh: '指标值', meaning: '该指标对应的统计数值' },
    ],
  },
];
