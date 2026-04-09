"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Form, Input, Button, message, Space, Modal } from "antd";
import { UserOutlined, LockOutlined, BankOutlined } from "@ant-design/icons";
import { login, register } from "@/lib/api";
import { saveToken } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [loginForm] = Form.useForm();
  const [registerForm] = Form.useForm();

  const handleLogin = async (values: { email: string; password: string }) => {
    setLoading(true);
    try {
      const data = await login(values.email, values.password);
      saveToken(data.access_token);
      message.success("登录成功");
      router.push("/dashboard");
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || "登录失败，请检查账号密码");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (values: {
    email: string;
    password: string;
    tenantName: string;
  }) => {
    setLoading(true);
    try {
      await register(values.email, values.password, values.tenantName);
      message.success("注册成功，请登录");
      setRegisterOpen(false);
      loginForm.setFieldsValue({ email: values.email });
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || "注册失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "#f0f2f5",
      }}
    >
      <Card
        title={
          <span style={{ fontSize: 20, fontWeight: 600 }}>
            ProfitLens 登录
          </span>
        }
        style={{ width: 400 }}
      >
        <Form form={loginForm} onFinish={handleLogin} layout="vertical">
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效邮箱" },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="邮箱" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>

          <Form.Item>
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Button type="primary" htmlType="submit" loading={loading}>
                登录
              </Button>
              <Button onClick={() => setRegisterOpen(true)}>注册</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Modal
        title="注册新账号"
        open={registerOpen}
        onCancel={() => setRegisterOpen(false)}
        footer={null}
      >
        <Form form={registerForm} onFinish={handleRegister} layout="vertical">
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效邮箱" },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="邮箱" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 6, message: "密码至少6个字符" },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>

          <Form.Item
            name="tenantName"
            label="公司名称"
            rules={[{ required: true, message: "请输入公司名称" }]}
          >
            <Input prefix={<BankOutlined />} placeholder="公司名称" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              注册
            </Button>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
