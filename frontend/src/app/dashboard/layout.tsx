"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Layout, Menu, Button, theme, Dropdown } from "antd";
import type { MenuProps } from "antd";
import {
  DashboardOutlined,
  BarChartOutlined,
  SwapOutlined,
  WarningOutlined,
  LineChartOutlined,
  SettingOutlined,
  ShopOutlined,
  InboxOutlined,
  UserOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import { isLoggedIn, clearToken } from "@/lib/auth";

const { Header, Sider, Content } = Layout;

const menuItems: MenuProps["items"] = [
  {
    key: "/dashboard",
    icon: <DashboardOutlined />,
    label: "总览",
  },
  {
    key: "/dashboard/sku-profit",
    icon: <BarChartOutlined />,
    label: "SKU利润",
  },
  {
    key: "/dashboard/cross-store",
    icon: <SwapOutlined />,
    label: "跨店对比",
  },
  {
    key: "/dashboard/unprofitable",
    icon: <WarningOutlined />,
    label: "亏损预警",
  },
  {
    key: "/dashboard/trends",
    icon: <LineChartOutlined />,
    label: "趋势",
  },
  {
    key: "settings",
    icon: <SettingOutlined />,
    label: "设置",
    children: [
      {
        key: "/dashboard/settings/stores",
        icon: <ShopOutlined />,
        label: "店铺",
      },
      {
        key: "/dashboard/settings/products",
        icon: <InboxOutlined />,
        label: "产品/成本",
      },
      {
        key: "/dashboard/settings/account",
        icon: <UserOutlined />,
        label: "账号",
      },
    ],
  },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [ready, setReady] = useState(false);

  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken();

  useEffect(() => {
    if (!isLoggedIn()) {
      router.replace("/login");
    } else {
      setReady(true);
    }
  }, [router]);

  const handleLogout = () => {
    clearToken();
    router.push("/login");
  };

  const userMenuItems: MenuProps["items"] = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: handleLogout,
    },
  ];

  const handleMenuClick: MenuProps["onClick"] = ({ key }) => {
    router.push(key);
  };

  if (!ready) return null;

  // Determine which menu keys to highlight
  const selectedKeys = [pathname];
  const openKeys = pathname.startsWith("/dashboard/settings")
    ? ["settings"]
    : [];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        style={{ background: colorBgContainer }}
      >
        <div
          style={{
            height: 48,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: collapsed ? 16 : 20,
            padding: "16px 0",
            color: "#1677ff",
          }}
        >
          {collapsed ? "PL" : "ProfitLens"}
        </div>
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          defaultOpenKeys={openKeys}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: "0 24px",
            background: colorBgContainer,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
          />
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Button type="text" icon={<UserOutlined />}>
              {collapsed ? "" : "我的账号"}
            </Button>
          </Dropdown>
        </Header>
        <Content
          style={{
            margin: 24,
            padding: 24,
            background: colorBgContainer,
            borderRadius: borderRadiusLG,
            minHeight: 360,
          }}
        >
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
