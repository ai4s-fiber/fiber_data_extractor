import { useEffect, useState } from 'react';
import { Card, Form, Input, Button, Select, Space, message, Alert, Spin } from 'antd';
import { SettingOutlined, SecurityScanOutlined, SaveOutlined } from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';

export default function SettingsPage() {
  const { currentProject } = useProject();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);

  useEffect(() => {
    if (currentProject) {
      setLoading(true);
      api.get(`/projects/${currentProject.id}/llm-config`)
        .then(res => {
          form.setFieldsValue({
            llm_provider: res.data.llm_provider || 'openai',
            llm_api_key: res.data.llm_api_key_masked || '',
            llm_base_url: res.data.llm_base_url || 'https://api.openai.com/v1',
            llm_model: res.data.llm_model || 'gpt-4o',
          });
        })
        .catch(() => {
          message.error('加载大模型配置失败');
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [currentProject, form]);

  const handleSave = async (values: any) => {
    if (!currentProject) {
      message.warning('请先在左侧选择一个项目');
      return;
    }
    setLoading(true);
    try {
      await api.put(`/projects/${currentProject.id}/llm-config`, values);
      message.success('保存大模型配置成功！');
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存配置失败');
    } finally {
      setLoading(false);
    }
  };

  const handleTestConnection = async () => {
    if (!currentProject) {
      message.warning('请先在左侧选择一个项目');
      return;
    }
    try {
      const values = await form.validateFields();
      setTestLoading(true);
      const res = await api.post(`/projects/${currentProject.id}/llm-config/test`, values);
      if (res.data.success) {
        message.success(res.data.message);
      } else {
        message.error(res.data.message || '连接失败，请检查 Base URL 或 API Key');
      }
    } catch (err: any) {
      if (err.errorFields) {
        message.warning('请先填写必填的配置项');
      } else {
        message.error(err.response?.data?.detail || '请求测试接口失败');
      }
    } finally {
      setTestLoading(false);
    }
  };

  if (!currentProject) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Alert message="提示" description="请先在左侧项目下拉菜单中选择一个项目以加载对应的大模型抽取参数。" type="info" showIcon />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 800, margin: '24px auto', padding: '0 16px' }}>
      <Spin spinning={loading}>
        <Card
          className="glass-card"
          title={
            <Space>
              <SettingOutlined style={{ color: 'var(--color-accent)' }} />
              <span>大模型提取服务配置 (项目专属)</span>
            </Space>
          }
          bordered={false}
        >
          <Alert
            message="科学防幻觉抽取引擎提示"
            description="纤维材料数据抽取 V6 支持为不同项目定制独立的 LLM API 通信网关。你可以将其接入国内低成本极速的 DeepSeek、GLM，亦或是 OpenAI。配置完毕后，后台的【必抽页面分词清单】与【样品目录先行】抽取流水线将真正调通你选择的模型接口！"
            type="info"
            showIcon
            style={{ marginBottom: 24 }}
          />

          <Form
            form={form}
            layout="vertical"
            onFinish={handleSave}
            initialValues={{
              llm_provider: 'openai',
              llm_base_url: 'https://api.openai.com/v1',
              llm_model: 'gpt-4o'
            }}
          >
            <Form.Item
              label="大模型服务提供商 (Provider)"
              name="llm_provider"
              rules={[{ required: true, message: '请选择提供商' }]}
            >
              <Select onChange={(val) => {
                if (val === 'openai') {
                  form.setFieldsValue({ llm_base_url: 'https://api.openai.com/v1', llm_model: 'gpt-4o' });
                } else if (val === 'anthropic') {
                  form.setFieldsValue({ llm_base_url: 'https://api.anthropic.com', llm_model: 'claude-sonnet-4-6' });
                }
              }}>
                <Select.Option value="openai">OpenAI (兼容 GPT-4o / DeepSeek 等 OpenAI 接口)</Select.Option>
                <Select.Option value="anthropic">Anthropic (Claude 系列模型)</Select.Option>
              </Select>
            </Form.Item>

            <Form.Item
              label="API 接口密钥 (API Key)"
              name="llm_api_key"
              rules={[{ required: true, message: '请输入 API 密钥以建立真实连接' }]}
            >
              <Input.Password
                placeholder="请输入形如 sk-xxxxxxxxxxxx 的 API 密钥"
                className="custom-input"
              />
            </Form.Item>

            <Form.Item
              label="接口服务基准地址 (API Base URL)"
              name="llm_base_url"
              rules={[{ required: true, message: '请输入接口 Base URL' }]}
            >
              <Input
                placeholder="如 https://api.openai.com/v1 或 https://api.deepseek.com/v1"
                className="custom-input"
              />
            </Form.Item>

            <Form.Item
              label="使用的模型名称 (Model)"
              name="llm_model"
              rules={[{ required: true, message: '请输入或选择模型名称' }]}
            >
              <Input
                placeholder="如 gpt-4o, deepseek-chat, glm-4"
                className="custom-input"
              />
            </Form.Item>

            <div style={{ marginTop: 32, display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
              <Button
                icon={<SecurityScanOutlined />}
                loading={testLoading}
                onClick={handleTestConnection}
                style={{ background: 'transparent', borderColor: 'var(--color-accent)', color: 'var(--color-accent)' }}
              >
                测试接口连通性
              </Button>
              
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                style={{ background: 'var(--color-accent)', borderColor: 'var(--color-accent)' }}
              >
                保存专属配置
              </Button>
            </div>
          </Form>
        </Card>
      </Spin>
    </div>
  );
}
