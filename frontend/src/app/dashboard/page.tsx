"use client";

import { useEffect, useState } from "react";
import { Row, Col, Card, Statistic, Spin, message } from "antd";
import {
  DollarOutlined,
  RiseOutlined,
  PercentageOutlined,
  ShoppingCartOutlined,
} from "@ant-design/icons";
import { getProfitSummary } from "@/lib/api";

interface SummaryData {
  total_revenue_rmb: number;
  total_profit_rmb: number;
  overall_margin_pct: number;
  total_units_sold: number;
  profit_by_store?: { store_name: string; profit_rmb: number }[];
}

export default function DashboardOverview() {
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<SummaryData | null>(null);

  useEffect(() => {
    const fetchSummary = async () => {
      try {
        const data = await getProfitSummary();
        setSummary(data);
      } catch {
        message.error("加载概览数据失败");
      } finally {
        setLoading(false);
      }
    };
    fetchSummary();
  }, []);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 24 }}>总览</h2>

      {/* KPI Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总收入 (RMB)"
              value={summary?.total_revenue_rmb ?? 0}
              precision={2}
              prefix={<DollarOutlined />}
              valueStyle={{ color: "#1677ff" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总利润 (RMB)"
              value={summary?.total_profit_rmb ?? 0}
              precision={2}
              prefix={<RiseOutlined />}
              valueStyle={{
                color:
                  (summary?.total_profit_rmb ?? 0) >= 0
                    ? "#52c41a"
                    : "#ff4d4f",
              }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="整体毛利率"
              value={summary?.overall_margin_pct ?? 0}
              precision={1}
              suffix="%"
              prefix={<PercentageOutlined />}
              valueStyle={{
                color:
                  (summary?.overall_margin_pct ?? 0) >= 0
                    ? "#52c41a"
                    : "#ff4d4f",
              }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总销量"
              value={summary?.total_units_sold ?? 0}
              prefix={<ShoppingCartOutlined />}
              valueStyle={{ color: "#1677ff" }}
            />
          </Card>
        </Col>
      </Row>

      {/* Profit by Store Bar Chart */}
      <Card title="各店铺利润 (RMB)" style={{ marginTop: 24 }}>
        {summary?.profit_by_store && summary.profit_by_store.length > 0 ? (
          <div>
            {summary.profit_by_store.map((store) => (
              <div
                key={store.store_name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  marginBottom: 12,
                }}
              >
                <span
                  style={{
                    width: 120,
                    flexShrink: 0,
                    textAlign: "right",
                    paddingRight: 12,
                  }}
                >
                  {store.store_name}
                </span>
                <div
                  style={{
                    height: 28,
                    width: `${Math.max(
                      Math.abs(store.profit_rmb) /
                        Math.max(
                          ...summary.profit_by_store!.map((s) =>
                            Math.abs(s.profit_rmb)
                          )
                        ) * 100,
                      2
                    )}%`,
                    background:
                      store.profit_rmb >= 0 ? "#52c41a" : "#ff4d4f",
                    borderRadius: 4,
                    display: "flex",
                    alignItems: "center",
                    paddingLeft: 8,
                    color: "#fff",
                    fontSize: 12,
                    minWidth: 60,
                  }}
                >
                  ¥{store.profit_rmb.toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ textAlign: "center", color: "#999", padding: 40 }}>
            暂无店铺数据，请先在「设置 → 店铺」中添加店铺并导入销售数据
          </div>
        )}
      </Card>
    </div>
  );
}
