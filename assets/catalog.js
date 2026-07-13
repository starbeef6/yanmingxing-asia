(function () {
  window.PORTAL_CONFIG = {
    apiBaseUrl: "https://file-hub-api-smuzijgeaq-as.a.run.app",
    departments: [
      {
        id: "supply-chain",
        name: "供应链",
        description: "供应链表格清洗、汇总、检查与对账",
        icon: "shipping",
        iconBackground: "#e8f2ff",
        iconColor: "#0071e3",
        tools: [
          {
            id: "supply-summary",
            name: "供应链汇总处理",
            description: "汇总和清洗供应链原始表",
            endpoint: "/api/tools/supply-summary/run",
            accept: ".xlsx,.xls,.csv",
            multiple: true,
            icon: "table"
          },
          {
            id: "inventory-check",
            name: "库存与缺货检查",
            description: "检查库存、缺货与商品异常",
            endpoint: "/api/tools/inventory-check/run",
            accept: ".xlsx,.xls,.csv",
            multiple: true,
            icon: "checklist"
          },
          {
            id: "vendor-reconcile",
            name: "供应商对账处理",
            description: "匹配供应商对账数据并输出差异",
            endpoint: "/api/tools/vendor-reconcile/run",
            accept: ".xlsx,.xls,.csv",
            multiple: true,
            icon: "compare"
          }
        ]
      }
    ]
  };
})();
