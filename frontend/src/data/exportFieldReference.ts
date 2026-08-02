/**
 * Field descriptions for the atomic materials-science workbook.
 * Column arrays must stay aligned with backend `material_data_model.py`.
 */

export interface FieldRef {
  no: number;
  name: string;
  meaning: string;
}

export interface ExportSheetRef {
  key: string;
  title: string;
  subtitle: string;
  description: string;
  fields: FieldRef[];
}

export const EXPORT_SHEET_COLUMN_NAMES = {
  '01_文献': [
    '文献ID', '文献标题', 'DOI', '发表年份', '期刊', '原始文件名', '元数据核验状态', '元数据备注',
  ],
  '02_样品总览': [
    '样品ID', '文献ID', '样品名称', '样品别名', '样品组', '材料体系', '材料形态', '基体',
    '配方摘要', '主要变量', '变量值', '变量单位', '处理状态',
  ],
  '03_成分': [
    '事实ID', '文献ID', '样品ID', '组分角色', '组分名称', '原始含量', '数值', '误差',
    '下限', '上限', '单位', '计量基准', '条件或说明',
  ],
  '04_工艺': [
    '事实ID', '文献ID', '样品ID', '工序序号', '工艺阶段', '工艺方法', '参数名称', '原始值',
    '数值', '误差', '下限', '上限', '单位', '设备或条件',
  ],
  '05_结构': [
    '事实ID', '文献ID', '样品ID', '结构类别', '指标名称', '原始指标名', '原始值', '数值',
    '误差', '下限', '上限', '单位', '表征方法', '测试条件',
  ],
  '06_性能': [
    '事实ID', '文献ID', '样品ID', '性能类别', '指标名称', '原始指标名', '原始值', '数值',
    '误差', '下限', '上限', '单位', '测试方法', '测试条件',
  ],
  '90_证据与质控': [
    '事实ID', '事实类别', '文献ID', '样品ID', '原始事实ID', '证据原文', '页码', '来源位置',
    '来源块', '置信度', '样品分配状态', '复核状态', '质控备注',
  ],
} as const;

const FIELD_MEANINGS: Record<string, string> = {
  文献ID: '工作簿内稳定的文献关联键',
  文献标题: '经清理和 DOI 核验的论文标题',
  DOI: '规范化后的数字对象标识符',
  发表年份: '按正式卷期统一的发表年份',
  期刊: '正式期刊名称',
  原始文件名: '本地导入时的 PDF 文件名',
  元数据核验状态: '是否已使用 DOI 来源核验',
  元数据备注: '标题、DOI、年份或期刊的修正说明',
  样品ID: '文献内唯一的实际样品或实验条件标识',
  样品名称: '样品在文献或抽取目录中的名称',
  样品别名: '正文、图表中出现的其它写法',
  样品组: '对照实验或变量系列的分组标识',
  材料体系: '基体与功能组分构成的材料体系',
  材料形态: '纤维、纳米纤维、薄膜等形态',
  基体: '样品的主体材料或前驱体',
  配方摘要: '样品配方的可读摘要',
  主要变量: '该样品系列主要变化的因素',
  变量值: '当前样品对应的变量值',
  变量单位: '主要变量的单位',
  处理状态: '未处理、溶剂处理、交联等状态',
  事实ID: '原子材料事实的稳定关联键',
  组分角色: '基体、功能组分、溶剂、交联剂等',
  组分名称: '材料或化学组分名称',
  原始含量: '文献中报告的含量原始写法',
  数值: '可解析时的中心数值',
  误差: '± 形式报告的误差',
  下限: '范围或误差区间下限',
  上限: '范围或误差区间上限',
  单位: '事实对应的物理或计量单位',
  计量基准: 'wt%、vol%、mg/mL、phr 等计量方式',
  条件或说明: '组分使用条件或必要说明',
  工序序号: '样品制备中的步骤顺序',
  工艺阶段: '成形、后处理、交联等阶段',
  工艺方法: '电纺、湿法纺丝、溶液浇铸等方法',
  参数名称: '温度、时间、电压、流量等参数名',
  原始值: '文献中的数值或描述原文写法',
  设备或条件: '设备、气氛及其它工艺条件',
  结构类别: '形貌尺寸、晶体结构、二级结构、光谱等',
  指标名称: '便于检索和比较的规范化指标名',
  原始指标名: '抽取时保留的原始指标名',
  表征方法: 'SEM、XRD、FTIR、Raman 等表征方法',
  测试条件: '测量或表征时的实验条件',
  性能类别: '力学、热学、电学、传输、物理性能等',
  测试方法: '性能测试方法、模型或标准',
  事实类别: '成分、工艺、结构或性能',
  原始事实ID: '抽取阶段生成的事实编号，供溯源',
  证据原文: '支撑当前事实的论文原文或表格内容',
  页码: '证据所在 PDF 页码',
  来源位置: '章节、图号、表号或其它位置说明',
  来源块: '解析器生成的原文块标识',
  置信度: '抽取结果的模型置信度',
  样品分配状态: '事实与样品的关联状态',
  复核状态: '待审核、通过、存疑或已修改',
  质控备注: '重复合并、样品归属修正等可解释记录',
};

const fieldsFor = (names: readonly string[]): FieldRef[] =>
  names.map((name, index) => ({
    no: index + 1,
    name,
    meaning: FIELD_MEANINGS[name] ?? name,
  }));

export const EXPORT_SHEET_REFERENCES: ExportSheetRef[] = [
  {
    key: '01_文献',
    title: '01_文献',
    subtitle: '一行一篇文献',
    description: '只保留科研检索需要的文献元数据，并记录必要的 DOI 校正说明。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['01_文献']),
  },
  {
    key: '02_样品总览',
    title: '02_样品总览',
    subtitle: '一行一个样品',
    description: '用于快速浏览文献中的样品、配方摘要、材料形态和实验变量。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['02_样品总览']),
  },
  {
    key: '03_成分',
    title: '03_成分',
    subtitle: '一行一个样品—组分关系',
    description: '基体、功能组分、溶剂和交联剂分别占一行，不再拼接在一个单元格中。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['03_成分']),
  },
  {
    key: '04_工艺',
    title: '04_工艺',
    subtitle: '一行一个工艺步骤或参数',
    description: '按样品和工序记录成形方法、处理步骤及可量化工艺参数。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['04_工艺']),
  },
  {
    key: '05_结构',
    title: '05_结构',
    subtitle: '一行一个结构事实',
    description: 'XRD、FTIR、形貌、尺寸、结晶度和二级结构等表征结果均在此表。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['05_结构']),
  },
  {
    key: '06_性能',
    title: '06_性能',
    subtitle: '一行一个性能事实',
    description: '力学、热学、电学、传输、释放和物理性能测量均在此表。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['06_性能']),
  },
  {
    key: '90_证据与质控',
    title: '90_证据与质控',
    subtitle: '一行一条证据或质控记录',
    description: '原文、页码、置信度、复核状态和自动修正说明集中放置，不污染科研主表。',
    fields: fieldsFor(EXPORT_SHEET_COLUMN_NAMES['90_证据与质控']),
  },
];
