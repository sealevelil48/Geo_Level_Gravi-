<?xml version="1.0" encoding="UTF-8"?>
<qgis version="3.0" styleCategories="AllStyleCategories">
  <renderer-v2 type="categorizedSymbol" attr="is_benchmark">
    <categories>
      <category symbol="0" value="true" label="Benchmark"/>
      <category symbol="1" value="false" label="Turning Point"/>
    </categories>
    <symbols>
      <symbol type="marker" name="0">
        <layer class="SimpleMarker" enabled="1">
          <prop k="color" v="0,0,255,255"/>
          <prop k="size" v="4"/>
          <prop k="name" v="triangle"/>
        </layer>
      </symbol>
      <symbol type="marker" name="1">
        <layer class="SimpleMarker" enabled="1">
          <prop k="color" v="255,165,0,255"/>
          <prop k="size" v="3"/>
          <prop k="name" v="circle"/>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings>
      <text-style fieldName="point_id" fontSize="8"/>
    </settings>
  </labeling>
</qgis>