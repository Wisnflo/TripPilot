const stop=(type,start,end,position,cost,extra={})=>({type,start_time:start,end_time:end,position,cost,transports:[],...extra});
export const visualExample={
  status:'design_example',replay:true,visualDemo:true,total:'1256.00',
  task:{start_city:'上海',target_city:'杭州',days:2,people_number:2,nature_language:'预算2000元，想去西湖散步，住一间双床房。'},
  plan:{start_city:'上海',target_city:'杭州',people_number:2,itinerary:[
    {day:1,activities:[
      stop('train','08:00','09:00',null,176,{start:'上海虹桥站',end:'杭州东站',TrainID:'G7371',tickets:2,price:88,description:'约1小时，二等座'}),
      stop('attraction','10:00','11:30','西湖风景名胜区',0,{description:'沿湖漫步，慢慢认识杭州。',address:'杭州市西湖区'}),
      stop('lunch','12:00','12:45','楼外楼（西湖景区店）',240,{description:'为午餐留一点从容的时间。',address:'杭州市西湖区孤山路30号'}),
      stop('attraction','14:00','16:00','苏堤漫步',0,{description:'沿着苏堤走走，看看湖光与远山。',address:'苏堤（西湖景区）',walking:true}),
      stop('attraction','16:30','17:30','雷峰塔',80,{description:'登高看看西湖，感受傍晚的光线。',address:'杭州市西湖区南山路15号'}),
      stop('dinner','18:30','19:30','河坊街美食街',80,{description:'逛一逛老街，找点喜欢的杭州味道。',address:'杭州市上城区河坊街'}),
      stop('accommodation','20:00','23:59','杭州西湖附近酒店',424,{rooms:1,description:'示例住宿安排；具体位置偏好尚待确认。',unverified:true}),
    ]},
    {day:2,activities:[
      stop('attraction','09:00','11:00','杭州博物馆',0,{description:'留一点时间，了解这座城市。'}),
      stop('lunch','12:00','12:45','湖畔午餐',80,{description:'一餐轻松的本地风味。'}),
      stop('attraction','14:00','16:00','湖滨散步',0,{description:'不赶时间，再看一眼西湖。',walking:true}),
      stop('train','19:00','20:00',null,176,{start:'杭州东站',end:'上海虹桥站',TrainID:'G7392',tickets:2,price:88}),
    ]},
  ]},
  unverified_preferences:['住宿位置偏好待确认'],sources:{},tools:[],requests:[],resources:{},
};
